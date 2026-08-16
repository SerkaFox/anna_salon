import hashlib
import logging
import re
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.mail import send_mail
from django.db.models import Q

from clients.models import Client
from whatsapp_bot.services import send_password_reset_credentials
from .identity import phone_variants_match


logger = logging.getLogger(__name__)
User = get_user_model()


def _phone_digits(value):
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[2:] if digits.startswith("00") else digits


def _find_client(identifier):
    value = (identifier or "").strip()
    if not value:
        return None
    clients = Client.objects.filter(is_active=True).select_related("user")
    username_match = clients.filter(user__username__iexact=value).first()
    if username_match:
        return username_match
    if "@" in value:
        matches = list(
            clients.filter(Q(email__iexact=value) | Q(user__email__iexact=value))[:2]
        )
        return matches[0] if len(matches) == 1 else None
    wanted_phone = _phone_digits(value)
    if len(wanted_phone) < 6:
        return None
    matches = [
        client
        for client in clients.exclude(Q(phone="") & Q(alternate_phone=""))
        if phone_variants_match(wanted_phone, client.phone)
        or phone_variants_match(wanted_phone, client.alternate_phone)
    ]
    return matches[0] if len(matches) == 1 else None


def _unique_username(client):
    phone = _phone_digits(client.phone or client.alternate_phone)
    email_name = (client.email or "").split("@", 1)[0]
    base = f"client_{phone[-6:]}" if len(phone) >= 6 else email_name
    base = re.sub(r"[^a-zA-Z0-9_.-]+", "_", base).strip("_.-")
    base = base or f"client_{client.pk}"
    candidate = base[:140]
    suffix = 1
    while User.objects.filter(username__iexact=candidate).exists():
        suffix += 1
        candidate = f"{base[:130]}_{suffix}"
    return candidate


def _temporary_password(length=10):
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _email_credentials(client, username, password):
    email = (client.email or getattr(client.user, "email", "") or "").strip()
    if not email:
        return False
    body = (
        f"Hola {client.first_name or client.full_name},\n\n"
        "Hemos creado un acceso temporal para BRIMOON Studio.\n"
        f"Usuario: {username}\n"
        f"Contraseña temporal: {password}\n\n"
        f"Acceso: {settings.PUBLIC_BASE_URL.rstrip('/')}/cuentas/login/\n\n"
        "Después de entrar, cambia la contraseña desde tu perfil."
    )
    try:
        send_mail(
            "Acceso temporal a BRIMOON Studio",
            body,
            getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@brimoon.es"),
            [email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Could not email password reset to client %s.", client.pk)
        return False
    return True


def request_client_password_recovery(identifier):
    """Deliver temporary credentials and activate them only after delivery."""
    normalized = (identifier or "").strip().lower()
    if not normalized:
        return False
    cache_key = "client-password-recovery:" + hashlib.sha256(normalized.encode()).hexdigest()
    if not cache.add(cache_key, True, timeout=120):
        return False
    client = _find_client(identifier)
    if client is None:
        return False

    user = client.user
    username = user.username if user else _unique_username(client)
    password = _temporary_password()
    whatsapp_sent = send_password_reset_credentials(
        client,
        username=username,
        password=password,
    )
    email_sent = _email_credentials(client, username, password)
    if not whatsapp_sent and not email_sent:
        return False

    if user is None:
        user = User(
            username=username,
            role=User.ROLE_CLIENT,
            first_name=client.first_name,
            last_name=client.last_name,
            email=client.email,
            phone=client.phone,
            is_active=True,
        )
    user.set_password(password)
    user.save()
    if client.user_id != user.pk:
        client.user = user
        client.save(update_fields=["user", "updated_at"])
    return True
