from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from bookings.models import Booking

from . import bridge
from .models import WhatsAppConnection, WhatsAppMessage, WhatsAppTemplate


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
    base_url = getattr(settings, "PUBLIC_BASE_URL", "").rstrip("/")
    ctx = {
        "client_name": booking.client.first_name or booking.client.full_name or "hola",
        "salon_name": _salon_name(),
        "date": local_start.strftime("%d/%m/%Y"),
        "time": local_start.strftime("%H:%M"),
        "service_name": booking.service.name,
        "portal_url": _portal_url(),
        "booking_url": f"{base_url}/panel/clientes/portal/bookings/{booking.pk}/",
    }
    body = WhatsAppTemplate.get_body(kind)
    if body:
        return body.format_map(ctx)
    return (
        f"Hola {ctx['client_name']}. Tu cita en {ctx['salon_name']} está confirmada para "
        f"el {ctx['date']} a las {ctx['time']}: {ctx['service_name']}."
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


def queue_welcome_credentials(booking, *, username, password):
    client = booking.client
    ctx = {
        "client_name": client.first_name or client.full_name or "hola",
        "salon_name": _salon_name(),
        "username": username,
        "password": password,
        "portal_url": _portal_url(),
    }
    tmpl = WhatsAppTemplate.get_body(WhatsAppMessage.Kinds.WELCOME_CREDENTIALS)
    body = tmpl.format_map(ctx) if tmpl else (
        f"Hola {ctx['client_name']} 👋 Bienvenida/o a {ctx['salon_name']}.\n\n"
        f"Usuario: {username}\nContraseña: {password}\n\n{ctx['portal_url']}"
    )
    connection = get_default_connection()
    phone = normalize_whatsapp_phone(client.phone)
    message = WhatsAppMessage(
        connection=connection,
        booking=booking,
        client=client,
        kind=WhatsAppMessage.Kinds.WELCOME_CREDENTIALS,
        to_phone=phone,
        body=body,
        scheduled_for=timezone.now(),
    )
    try:
        with transaction.atomic():
            message.save()
    except IntegrityError:
        return WhatsAppMessage.objects.get(booking=booking, kind=WhatsAppMessage.Kinds.WELCOME_CREDENTIALS), False
    return message, True


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


def queue_and_send(booking, *, kind):
    """Queue a booking notification and dispatch it immediately."""
    msg, created = queue_booking_message(booking, kind=kind)
    if created:
        send_whatsapp_message(msg)
    return msg, created


def queue_and_send_waitlist_message(entry, *, kind, to_phone, context):
    phone = normalize_whatsapp_phone(to_phone)
    if not phone:
        return None, False
    body_template = WhatsAppTemplate.get_body(kind)
    if not body_template:
        return None, False
    message = WhatsAppMessage.objects.create(
        connection=get_default_connection(),
        client=entry.client,
        kind=kind,
        to_phone=phone,
        body=body_template.format_map(context),
        scheduled_for=timezone.now(),
    )
    send_whatsapp_message(message)
    return message, True


def notify_waitlist_entry_created(entry):
    target_phone = (
        getattr(settings, 'WHATSAPP_WAITLIST_NOTIFICATION_PHONE', '')
        or entry.employee.phone
    )
    context = {
        'client_name': entry.name,
        'salon_name': _salon_name(),
        'service_name': entry.service.name,
        'date': entry.desired_date.strftime('%d/%m/%Y'),
        'time_range': entry.time_range or 'cualquier hora',
        'phone': entry.phone or '-',
        'email': entry.email or '-',
    }
    return queue_and_send_waitlist_message(
        entry,
        kind=WhatsAppMessage.Kinds.WAITLIST_JOINED,
        to_phone=target_phone,
        context=context,
    )


def notify_waitlist_slot_available(entry, booking):
    local_start = timezone.localtime(booking.start_at)
    base_url = getattr(settings, 'PUBLIC_BASE_URL', '').rstrip('/')
    context = {
        'client_name': entry.name,
        'salon_name': _salon_name(),
        'service_name': booking.service.name,
        'employee_name': booking.employee.full_name,
        'date': local_start.strftime('%d/%m/%Y'),
        'time': local_start.strftime('%H:%M'),
        'booking_url': f'{base_url}/reservar/',
    }
    return queue_and_send_waitlist_message(
        entry,
        kind=WhatsAppMessage.Kinds.WAITLIST_SLOT_AVAILABLE,
        to_phone=entry.phone,
        context=context,
    )


def queue_birthday_greeting(client):
    from django.utils import timezone as tz
    ctx = {
        "client_name": client.first_name or client.full_name or "hola",
        "salon_name": _salon_name(),
        "offer": getattr(settings, "WHATSAPP_BIRTHDAY_OFFER", "un descuento especial en tu próxima visita"),
    }
    tmpl = WhatsAppTemplate.get_body(WhatsAppMessage.Kinds.BIRTHDAY_GREETING)
    body = tmpl.format_map(ctx) if tmpl else (
        f"Hola {ctx['client_name']} 🎂 ¡Feliz cumpleaños de parte de {ctx['salon_name']}! "
        f"Como regalo: {ctx['offer']} 🎁"
    )
    phone = normalize_whatsapp_phone(client.phone)
    if not phone:
        return None, False
    connection = get_default_connection()
    # Prevent duplicate: skip if sent in the last 300 days
    cutoff = tz.now() - timedelta(days=300)
    if WhatsAppMessage.objects.filter(
        client=client,
        kind=WhatsAppMessage.Kinds.BIRTHDAY_GREETING,
        created_at__gte=cutoff,
    ).exists():
        return None, False
    msg = WhatsAppMessage(
        connection=connection,
        client=client,
        kind=WhatsAppMessage.Kinds.BIRTHDAY_GREETING,
        to_phone=phone,
        body=body,
        scheduled_for=tz.now(),
    )
    msg.save()
    return msg, True


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
