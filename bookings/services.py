import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Q
from django.utils import timezone

from payments.models import Payment as OnlinePayment
from salon.preferences import calculate_deposit_amount

from .models import Booking, BookingPrepayment, BookingWaitlistEntry


logger = logging.getLogger(__name__)


def booking_group_members(booking):
    """All bookings created together with this one (itself included)."""
    if not booking.booking_group_id:
        return Booking.objects.filter(pk=booking.pk)
    return Booking.objects.filter(booking_group_id=booking.booking_group_id)


def booking_group_holder(booking):
    """The group's first booking, which carries the shared online prepayment."""
    if not booking.booking_group_id:
        return booking
    return booking_group_members(booking).order_by("pk").first() or booking


def is_booking_group_member(booking):
    """True for a non-holder booking whose prepayment is covered by the holder."""
    return bool(booking.booking_group_id) and booking_group_holder(booking).pk != booking.pk


def active_group_siblings(booking):
    """Other bookings of the same group that have not been cancelled."""
    if not booking.booking_group_id:
        return Booking.objects.none()
    return (
        booking_group_members(booking)
        .exclude(pk=booking.pk)
        .exclude(status=Booking.Statuses.CANCELLED)
    )


def booking_group_total(booking):
    """Client price of the whole (non-cancelled) group, or of the booking alone."""
    if not booking.booking_group_id:
        return booking.client_price_snapshot or booking.price_snapshot or Decimal("0.00")
    total = Decimal("0.00")
    for member in booking_group_members(booking).exclude(
        status=Booking.Statuses.CANCELLED
    ):
        total += member.client_price_snapshot or member.price_snapshot or Decimal("0.00")
    return total


def booking_group_service_names(booking):
    members = booking_group_members(booking).exclude(status=Booking.Statuses.CANCELLED)
    return " + ".join(member.service_names for member in members.order_by("pk"))


def group_prepayment_paid(booking):
    """True when the group's holder has a captured online payment."""
    holder = booking_group_holder(booking)
    return holder.online_payments.filter(status=OnlinePayment.Statuses.PAID).exists()


def confirm_booking_group(booking):
    """Confirm the pending siblings once the group's shared prepayment is paid."""
    if not booking.booking_group_id:
        return 0
    return active_group_siblings(booking).filter(
        status=Booking.Statuses.PENDING
    ).update(status=Booking.Statuses.CONFIRMED, updated_at=timezone.now())


def sync_group_prepayment_window(holder):
    """Give the group's siblings the same prepayment window as the holder."""
    if not holder.booking_group_id:
        return 0
    return active_group_siblings(holder).exclude(
        status__in={Booking.Statuses.DONE, Booking.Statuses.NO_SHOW}
    ).update(
        prepayment_policy=holder.prepayment_policy,
        prepayment_requested_at=holder.prepayment_requested_at,
        prepayment_deadline_at=holder.prepayment_deadline_at,
        status=Booking.Statuses.PENDING,
        updated_at=timezone.now(),
    )


def calculate_booking_prepayment_amount(booking):
    total = booking.client_price_snapshot or booking.price_snapshot or Decimal("0.00")
    return calculate_deposit_amount(total)


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
    from whatsapp_bot.services import notify_waitlist_slot_available

    booking_date = timezone.localtime(booking.start_at).date()
    entries = list(
        BookingWaitlistEntry.objects.select_related("employee", "service")
        .filter(
            status=BookingWaitlistEntry.Statuses.ACTIVE,
            employee=booking.employee,
            service=booking.service,
            desired_date__lte=booking_date,
        )
        .filter(Q(desired_date_to__isnull=True) | Q(desired_date_to__gte=booking_date))
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
        try:
            notify_waitlist_slot_available(entry, booking)
        except Exception:
            logger.exception('Could not send WhatsApp waitlist notification for entry %s', entry.pk)

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
