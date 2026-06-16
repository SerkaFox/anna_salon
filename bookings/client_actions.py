from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from payments.models import Payment
from payments.stripe_service import create_checkout_session, create_pending_stripe_payment, create_refund
from services_app.models import Service

from .models import Booking
from .services import notify_waitlist_for_booking_opening
from .utils import find_available_zone, is_slot_available


def booking_refundable_until(booking):
    hours = getattr(settings, "BOOKING_FREE_CANCEL_HOURS", 24)
    return booking.start_at - timedelta(hours=hours)


def booking_paid_amount(booking):
    paid = sum(
        (payment.amount for payment in booking.online_payments.filter(status__in={
            Payment.Statuses.PAID,
            Payment.Statuses.PARTIALLY_REFUNDED,
            Payment.Statuses.REFUND_PENDING,
        })),
        Decimal("0.00"),
    )
    refunded = sum((payment.amount_refunded for payment in booking.online_payments.all()), Decimal("0.00"))
    return max(paid - refunded, Decimal("0.00"))


def booking_amount_due(booking):
    total = booking.client_price_snapshot or booking.price_snapshot or Decimal("0.00")
    return max(total - booking_paid_amount(booking), Decimal("0.00")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def can_client_cancel(booking):
    return booking.status not in {Booking.Statuses.CANCELLED, Booking.Statuses.DONE, Booking.Statuses.NO_SHOW}


def can_client_reschedule(booking):
    return can_client_cancel(booking) and timezone.now() <= booking_refundable_until(booking)


def cancel_booking(booking):
    if not can_client_cancel(booking):
        raise ValidationError("Esta reserva no se puede cancelar.")

    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking.pk)
        refundable = timezone.now() <= booking_refundable_until(booking)
        refunds = []
        if refundable:
            for payment in booking.online_payments.select_for_update().filter(provider=Payment.Providers.STRIPE):
                if payment.is_refundable:
                    refunds.append(create_refund(payment))

        booking.status = Booking.Statuses.CANCELLED
        booking.save(update_fields=["status", "updated_at"])
        prepayment = getattr(booking, "prepayment", None)
        if prepayment and refundable:
            prepayment.status = prepayment.Statuses.REFUNDED
            prepayment.refunded_at = timezone.now()
            prepayment.save(update_fields=["status", "refunded_at", "updated_at"])
        elif prepayment and not refundable:
            prepayment.refresh_forfeit_status()
        notify_waitlist_for_booking_opening(booking)
    if refundable and refunds:
        return "La reserva se ha cancelado. Se ha solicitado la devolución automática de la señal.", refunds
    if refundable:
        return "La reserva se ha cancelado.", refunds
    return "La reserva se ha cancelado. La señal no es reembolsable porque faltan menos de 24 horas para la cita.", refunds


def reschedule_booking(booking, *, start_at, employee=None, zone=None, allow_late=False):
    if not allow_late and not can_client_reschedule(booking):
        raise ValidationError("No se puede cambiar la cita con menos de 24 horas de antelación. Contacta con el salón.")
    employee = employee or booking.employee
    service = booking.service
    end_at = start_at + timedelta(minutes=booking.duration_snapshot or service.duration_minutes)
    if service.requires_zone and zone is None:
        zone = find_available_zone(service, start_at, end_at, exclude_booking_id=booking.pk)
    if not employee.services.filter(pk=service.pk).exists():
        raise ValidationError("Este empleado no realiza el servicio seleccionado.")
    if not is_slot_available(employee, service, zone, start_at, end_at, exclude_booking_id=booking.pk):
        raise ValidationError("El horario seleccionado no está disponible.")
    booking.employee = employee
    booking.zone = zone
    booking.start_at = start_at
    booking.end_at = end_at
    booking.save(update_fields=["employee", "zone", "start_at", "end_at", "updated_at"])
    return booking


def _apply_service_snapshots(booking, service):
    original_price = service.price or Decimal("0.00")
    client_price = original_price
    employee_percent = getattr(booking.employee, "commission_percent", Decimal("40.00")) or Decimal("40.00")
    employee_amount = (client_price * employee_percent / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    salon_amount = (client_price - employee_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    booking.service = service
    booking.price_snapshot = original_price
    booking.original_client_price_snapshot = original_price
    booking.client_price_snapshot = client_price
    booking.discount_amount_snapshot = Decimal("0.00")
    booking.duration_snapshot = service.duration_minutes
    booking.employee_percent_snapshot = employee_percent
    booking.employee_amount_snapshot = employee_amount
    booking.salon_amount_snapshot = salon_amount


def change_booking_service(booking, *, service, request=None):
    if not isinstance(service, Service):
        service = Service.objects.get(pk=service)
    if not booking.employee.services.filter(pk=service.pk).exists():
        raise ValidationError("Este empleado no realiza el servicio seleccionado.")

    old_total = booking.client_price_snapshot or booking.price_snapshot or Decimal("0.00")
    paid_amount = booking_paid_amount(booking)
    new_end_at = booking.start_at + timedelta(minutes=service.duration_minutes)
    zone = booking.zone
    if service.requires_zone and zone is None:
        zone = find_available_zone(service, booking.start_at, new_end_at, exclude_booking_id=booking.pk)
    if not is_slot_available(booking.employee, service, zone, booking.start_at, new_end_at, exclude_booking_id=booking.pk):
        raise ValidationError("No hay disponibilidad para la nueva duración del servicio.")

    _apply_service_snapshots(booking, service)
    booking.zone = zone
    booking.end_at = new_end_at
    booking.save(
        update_fields=[
            "service",
            "zone",
            "end_at",
            "price_snapshot",
            "original_client_price_snapshot",
            "client_price_snapshot",
            "discount_amount_snapshot",
            "duration_snapshot",
            "employee_percent_snapshot",
            "employee_amount_snapshot",
            "salon_amount_snapshot",
            "updated_at",
        ]
    )

    new_total = booking.client_price_snapshot or Decimal("0.00")
    extra_due = max(new_total - paid_amount, Decimal("0.00")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    manual_refund_required = paid_amount > new_total
    payment = None
    if extra_due > Decimal("0.00") and request is not None:
        payment = create_pending_stripe_payment(
            booking,
            amount=extra_due,
            status=Payment.Statuses.EXTRA_PAYMENT_PENDING,
            reason="booking_extra_payment",
        )
        create_checkout_session(payment, request)
    return {
        "booking": booking,
        "old_total": old_total,
        "new_total": new_total,
        "paid_amount": paid_amount,
        "extra_due": extra_due,
        "payment": payment,
        "manual_refund_required": manual_refund_required,
    }
