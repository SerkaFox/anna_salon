"""Unified notification dispatcher — WhatsApp and/or Email depending on
client preferences and available contact details."""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from whatsapp_bot.services import (
    get_default_connection,
    normalize_whatsapp_phone,
    _salon_name,
    _portal_url,
    send_whatsapp_message,
)
from whatsapp_bot.models import WhatsAppMessage

logger = logging.getLogger(__name__)


def _from_email():
    return getattr(settings, "DEFAULT_FROM_EMAIL", f"no-reply@{_salon_domain()}")


def _salon_domain():
    base = getattr(settings, "PUBLIC_BASE_URL", "").rstrip("/")
    if "://" in base:
        return base.split("://", 1)[1].lstrip("www.")
    return "brimoon.es"


def _send_email(to_email, subject, body):
    try:
        send_mail(subject, body, _from_email(), [to_email], fail_silently=True)
    except Exception:
        logger.exception("Email send failed to %s", to_email)


def _send_whatsapp(client, body, *, booking=None, kind=WhatsAppMessage.Kinds.MANUAL):
    if not getattr(settings, "WHATSAPP_SEND_CONFIRMATION_ON_BOOKING", True):
        return
    phone = normalize_whatsapp_phone(client.phone)
    if not phone:
        return
    connection = get_default_connection()
    from django.db import IntegrityError, transaction
    msg = WhatsAppMessage(
        connection=connection,
        booking=booking,
        client=client,
        kind=kind,
        to_phone=phone,
        body=body,
        scheduled_for=timezone.now(),
    )
    try:
        with transaction.atomic():
            msg.save()
        send_whatsapp_message(msg)
    except IntegrityError:
        # Duplicate — try to send existing queued one
        existing = WhatsAppMessage.objects.filter(booking=booking, kind=kind).first()
        if existing and existing.status == WhatsAppMessage.Statuses.QUEUED:
            send_whatsapp_message(existing)
    except Exception:
        logger.exception("WhatsApp send failed for client %s", client.pk)


def _channels(client):
    """Return (send_wa, send_email) based on preferences and available contacts."""
    has_phone = bool(client.phone)
    has_email = bool(client.email)
    send_wa = has_phone and client.notify_whatsapp
    send_em = has_email and client.notify_email
    # Fallback: if only email, still notify even if notify_email is default True
    return send_wa, send_em


def notify_booking_confirmation(booking):
    client = booking.client
    local_start = timezone.localtime(booking.start_at)
    date_text = local_start.strftime("%d/%m/%Y")
    time_text = local_start.strftime("%H:%M")
    salon = _salon_name()
    base = getattr(settings, "PUBLIC_BASE_URL", "").rstrip("/")
    portal = f"{base}/panel/clientes/portal/bookings/{booking.pk}/"
    client_name = client.first_name or client.full_name or "hola"
    service_name = booking.service_names

    body = (
        f"Hola {client_name} 👋 Tu cita en {salon} está confirmada:\n"
        f"📅 {date_text} a las {time_text}\n"
        f"💅 {service_name}\n\n"
        f"Para pagar la señal y confirmar tu reserva accede a tu área privada:\n"
        f"🔗 {portal}"
    )

    send_wa, send_em = _channels(client)
    if send_wa:
        _send_whatsapp(client, body, booking=booking,
                       kind=WhatsAppMessage.Kinds.BOOKING_CONFIRMATION)
    if send_em:
        _send_email(
            client.email,
            f"✅ Cita confirmada en {salon} — {date_text} {time_text}",
            body,
        )


def notify_booking_cancelled(booking):
    client = booking.client
    local_start = timezone.localtime(booking.start_at)
    date_text = local_start.strftime("%d/%m/%Y")
    time_text = local_start.strftime("%H:%M")
    salon = _salon_name()
    portal = _portal_url()
    client_name = client.first_name or client.full_name or "hola"
    service_name = booking.service_names

    body = (
        f"Hola {client_name}. Tu cita en {salon} del {date_text} "
        f"a las {time_text} ({service_name}) ha sido cancelada.\n"
        f"Si quieres volver a reservar: {portal}"
    )

    send_wa, send_em = _channels(client)
    if send_wa:
        _send_whatsapp(client, body, booking=booking,
                       kind=WhatsAppMessage.Kinds.BOOKING_CANCELLED)
    if send_em:
        _send_email(
            client.email,
            f"❌ Cita cancelada en {salon} — {date_text}",
            body,
        )


def notify_booking_rescheduled(booking):
    client = booking.client
    local_start = timezone.localtime(booking.start_at)
    date_text = local_start.strftime("%d/%m/%Y")
    time_text = local_start.strftime("%H:%M")
    salon = _salon_name()
    portal = _portal_url()
    client_name = client.first_name or client.full_name or "hola"
    service_name = booking.service_names

    body = (
        f"Hola {client_name}. Tu cita en {salon} ha sido reagendada:\n"
        f"📅 {date_text} a las {time_text}\n"
        f"💅 {service_name}\n\n"
        f"Ver detalles: {portal}"
    )

    send_wa, send_em = _channels(client)
    if send_wa:
        _send_whatsapp(client, body, booking=booking,
                       kind=WhatsAppMessage.Kinds.BOOKING_RESCHEDULED)
    if send_em:
        _send_email(
            client.email,
            f"🔄 Cita reagendada en {salon} — {date_text}",
            body,
        )


def notify_payment_receipt(booking, payment):
    client = booking.client
    local_start = timezone.localtime(booking.start_at)
    date_text = local_start.strftime("%d/%m/%Y")
    time_text = local_start.strftime("%H:%M")
    salon = _salon_name()
    base = getattr(settings, "PUBLIC_BASE_URL", "").rstrip("/")
    portal = f"{base}/panel/clientes/portal/bookings/{booking.pk}/"
    client_name = client.first_name or client.full_name or "hola"
    service_name = booking.service_names
    amount = f"{payment.amount:.2f} {payment.currency.upper()}"

    body = (
        f"✅ Pago recibido — {salon}\n\n"
        f"Hola {client_name}, hemos recibido tu pago:\n"
        f"💳 {amount}\n"
        f"💅 {service_name}\n"
        f"📅 {date_text} a las {time_text}\n\n"
        f"Ver tu reserva: {portal}"
    )

    send_wa, send_em = _channels(client)
    if send_wa:
        _send_whatsapp(client, body, booking=booking,
                       kind=WhatsAppMessage.Kinds.PAYMENT_RECEIPT)
    if send_em:
        _send_email(
            client.email,
            f"✅ Pago recibido — {salon} — {service_name} {date_text}",
            body,
        )


def notify_welcome_credentials(booking, *, username, password):
    client = booking.client
    salon = _salon_name()
    base = getattr(settings, "PUBLIC_BASE_URL", "").rstrip("/")
    login_url = f"{base}/panel/clientes/portal/"
    client_name = client.first_name or client.full_name or "hola"

    body = (
        f"Hola {client_name} 👋 Bienvenida/o a {salon}.\n\n"
        f"Tus datos de acceso al área privada:\n"
        f"🔑 Usuario: {username}\n"
        f"🔒 Contraseña: {password}\n\n"
        f"Accede aquí cuando quieras:\n"
        f"🔗 {login_url}\n\n"
        f"Guarda este mensaje para no perder tus datos."
    )

    send_wa, send_em = _channels(client)
    if send_wa:
        _send_whatsapp(client, body, booking=booking,
                       kind=WhatsAppMessage.Kinds.WELCOME_CREDENTIALS)
    if send_em:
        _send_email(
            client.email,
            f"Bienvenida/o a {salon} — tus datos de acceso",
            body,
        )
