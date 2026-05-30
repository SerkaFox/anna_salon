from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from payments.models import Payment as OnlinePayment

from .models import Booking, BookingPrepayment, BookingWaitlistEntry


def create_booking_prepayment(booking, payment):
    prepayment, _created = BookingPrepayment.objects.update_or_create(
        booking=booking,
        defaults={
            "payment": payment,
            "amount": payment.amount,
            "status": BookingPrepayment.Statuses.PAID,
            "refundable_until": booking.start_at - timedelta(hours=24),
            "refunded_at": None,
            "forfeited_at": None,
        },
    )
    return prepayment


def refund_booking_prepayment(prepayment):
    if not prepayment.is_refundable:
        return False, "La devolucion solo esta disponible hasta 24 horas antes de la cita."

    prepayment.status = BookingPrepayment.Statuses.REFUNDED
    prepayment.refunded_at = timezone.now()
    prepayment.save(update_fields=["status", "refunded_at", "updated_at"])

    if prepayment.payment_id:
        prepayment.payment.status = OnlinePayment.Statuses.REFUNDED
        prepayment.payment.save(update_fields=["status", "updated_at"])

    booking = prepayment.booking
    if booking.status not in {Booking.Statuses.CANCELLED, Booking.Statuses.NO_SHOW, Booking.Statuses.DONE}:
        booking.status = Booking.Statuses.CANCELLED
        booking.save(update_fields=["status", "updated_at"])
        notify_waitlist_for_booking_opening(booking)

    return True, "Prepago devuelto. La reserva ha sido cancelada."


def refresh_booking_prepayments(bookings):
    for booking in bookings:
        prepayment = getattr(booking, "prepayment", None)
        if prepayment:
            prepayment.refresh_forfeit_status()
    return bookings


def notify_waitlist_for_booking_opening(booking):
    entries = list(
        BookingWaitlistEntry.objects.select_related("employee", "service")
        .filter(
            status=BookingWaitlistEntry.Statuses.ACTIVE,
            employee=booking.employee,
            service=booking.service,
            desired_date=timezone.localtime(booking.start_at).date(),
        )
        .order_by("created_at")
    )
    if not entries:
        return 0

    start_label = timezone.localtime(booking.start_at).strftime("%d/%m/%Y %H:%M")
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@brimoon.es")
    notified_count = 0
    for entry in entries:
        client_subject = "Se ha liberado una cita en BRIMOON Studio"
        client_body = (
            f"Hola {entry.name},\n\n"
            f"Se ha liberado un hueco con {booking.employee.full_name} para {booking.service.name}: {start_label}.\n"
            "Entra en tu cuenta o contacta con BRIMOON Studio para reservarlo."
        )
        recipients = [entry.email] if entry.email else []
        if recipients:
            send_mail(client_subject, client_body, from_email, recipients, fail_silently=True)

        if booking.employee.email:
            master_body = (
                f"Hay una persona en lista de espera para el hueco liberado {start_label}.\n\n"
                f"Cliente: {entry.name}\nTelefono: {entry.phone or '-'}\nEmail: {entry.email or '-'}"
            )
            send_mail(
                "Lista de espera BRIMOON Studio",
                master_body,
                from_email,
                [booking.employee.email],
                fail_silently=True,
            )

        entry.status = BookingWaitlistEntry.Statuses.NOTIFIED
        entry.notified_at = timezone.now()
        entry.save(update_fields=["status", "notified_at", "updated_at"])
        notified_count += 1

    return notified_count
