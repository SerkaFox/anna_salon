from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from bookings.models import Booking

from . import bridge
from .models import WhatsAppConnection, WhatsAppMessage


def get_default_connection():
    name = getattr(settings, "WHATSAPP_CONNECTION_NAME", "main")
    connection, _created = WhatsAppConnection.objects.get_or_create(name=name)
    return connection


def normalize_whatsapp_phone(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    country_code = str(getattr(settings, "WHATSAPP_DEFAULT_COUNTRY_CODE", "34")).lstrip("+")
    local_length = int(getattr(settings, "WHATSAPP_LOCAL_PHONE_LENGTH", "9"))
    if len(digits) == local_length:
        digits = f"{country_code}{digits}"
    return f"+{digits}"


def _salon_name():
    return getattr(settings, "SALON_NAME", "BRIMOON Studio")


def _portal_url():
    base = getattr(settings, "PUBLIC_BASE_URL", "").rstrip("/")
    return f"{base}/panel/clientes/portal/"


def booking_message(booking, *, kind):
    local_start = timezone.localtime(booking.start_at)
    date_text = local_start.strftime("%d/%m/%Y")
    time_text = local_start.strftime("%H:%M")
    service_name = booking.service.name
    client_name = booking.client.first_name or booking.client.full_name or "hola"
    salon = _salon_name()
    portal = _portal_url()

    if kind == WhatsAppMessage.Kinds.BOOKING_CONFIRMATION:
        booking_url = f"{portal.rstrip('/')}/../bookings/{booking.pk}/"
        # clean up double slashes
        import re
        booking_url = re.sub(r"(?<!:)//+", "/", booking_url)
        return (
            f"Hola {client_name} 👋 Tu cita en {salon} está confirmada:\n"
            f"📅 {date_text} a las {time_text}\n"
            f"💅 {service_name}\n\n"
            f"Para pagar la señal y confirmar tu reserva accede a tu área privada:\n"
            f"🔗 {booking_url}"
        )
    if kind == WhatsAppMessage.Kinds.BOOKING_CANCELLED:
        return (
            f"Hola {client_name}. Tu cita en {salon} del {date_text} "
            f"a las {time_text} ({service_name}) ha sido cancelada.\n"
            f"Si quieres volver a reservar: {portal}"
        )
    if kind == WhatsAppMessage.Kinds.BOOKING_RESCHEDULED:
        return (
            f"Hola {client_name}. Tu cita en {salon} ha sido reagendada:\n"
            f"📅 {date_text} a las {time_text}\n"
            f"💅 {service_name}\n\n"
            f"Ver detalles: {portal}"
        )
    if kind == WhatsAppMessage.Kinds.REMINDER_24H:
        return (
            f"Hola {client_name} 👋 Te recordamos tu cita en {salon} mañana "
            f"{date_text} a las {time_text} para {service_name}."
        )
    if kind == WhatsAppMessage.Kinds.REMINDER_2H:
        return (
            f"Hola {client_name} 👋 Te esperamos en {salon} en 2 horas, "
            f"a las {time_text}, para {service_name}."
        )
    return (
        f"Hola {client_name}. Tu cita en {salon} está confirmada para "
        f"el {date_text} a las {time_text}: {service_name}."
    )


def queue_booking_message(booking, *, kind, scheduled_for=None):
    connection = get_default_connection()
    phone = normalize_whatsapp_phone(booking.client.phone)
    message = WhatsAppMessage(
        connection=connection,
        booking=booking,
        client=booking.client,
        kind=kind,
        to_phone=phone,
        body=booking_message(booking, kind=kind),
        scheduled_for=scheduled_for or timezone.now(),
    )
    try:
        with transaction.atomic():
            message.save()
    except IntegrityError:
        return WhatsAppMessage.objects.get(booking=booking, kind=kind), False
    return message, True


def queue_booking_confirmation(booking):
    return queue_booking_message(booking, kind=WhatsAppMessage.Kinds.BOOKING_CONFIRMATION)


def queue_booking_cancellation(booking):
    return queue_booking_message(booking, kind=WhatsAppMessage.Kinds.BOOKING_CANCELLED)


def queue_booking_rescheduled(booking):
    # Delete previous reschedule message for this booking so we can send a fresh one.
    WhatsAppMessage.objects.filter(
        booking=booking, kind=WhatsAppMessage.Kinds.BOOKING_RESCHEDULED
    ).delete()
    connection = get_default_connection()
    phone = normalize_whatsapp_phone(booking.client.phone)
    message = WhatsAppMessage(
        connection=connection,
        booking=booking,
        client=booking.client,
        kind=WhatsAppMessage.Kinds.BOOKING_RESCHEDULED,
        to_phone=phone,
        body=booking_message(booking, kind=WhatsAppMessage.Kinds.BOOKING_RESCHEDULED),
        scheduled_for=timezone.now(),
    )
    message.save()
    return message, True


def queue_due_reminders(*, hours, window_minutes=15):
    if hours not in (2, 24):
        raise ValueError("Only 2h and 24h reminders are supported.")
    kind = WhatsAppMessage.Kinds.REMINDER_2H if hours == 2 else WhatsAppMessage.Kinds.REMINDER_24H
    window_start = timezone.now() + timedelta(hours=hours)
    window_end = window_start + timedelta(minutes=window_minutes)
    bookings = (
        Booking.objects.select_related("client", "service", "employee", "zone")
        .filter(start_at__gte=window_start, start_at__lt=window_end)
        .exclude(status__in=[Booking.Statuses.CANCELLED, Booking.Statuses.NO_SHOW, Booking.Statuses.DONE])
        .order_by("start_at", "id")
    )
    queued = []
    skipped = []
    for booking in bookings:
        message, created = queue_booking_message(booking, kind=kind)
        if created:
            queued.append(message)
        else:
            skipped.append(message)
    return {"queued": queued, "skipped": skipped}


def send_whatsapp_message(message):
    if not message.to_phone:
        message.status = WhatsAppMessage.Statuses.SKIPPED
        message.error = "Client has no WhatsApp phone."
        message.save(update_fields=["status", "error", "updated_at"])
        return message

    if getattr(settings, "WHATSAPP_DRY_RUN", True):
        message.status = WhatsAppMessage.Statuses.SENT
        message.provider_message_id = "dry-run"
        message.sent_at = timezone.now()
        message.error = ""
        message.save(update_fields=["status", "provider_message_id", "sent_at", "error", "updated_at"])
        return message

    try:
        result = bridge.send_message(message.connection, to_phone=message.to_phone, body=message.body)
    except bridge.WhatsAppBridgeError as exc:
        message.status = WhatsAppMessage.Statuses.FAILED
        message.error = str(exc)
        message.save(update_fields=["status", "error", "updated_at"])
        return message

    message.status = WhatsAppMessage.Statuses.SENT
    message.provider_message_id = str(result.get("message_id") or result.get("id") or "")
    message.sent_at = timezone.now()
    message.error = ""
    message.save(update_fields=["status", "provider_message_id", "sent_at", "error", "updated_at"])
    return message


def send_due_messages(*, limit=50):
    messages = (
        WhatsAppMessage.objects.select_related("connection", "booking", "client")
        .filter(status=WhatsAppMessage.Statuses.QUEUED, scheduled_for__lte=timezone.now())
        .order_by("scheduled_for", "id")[:limit]
    )
    sent = []
    for message in messages:
        sent.append(send_whatsapp_message(message))
    return sent
