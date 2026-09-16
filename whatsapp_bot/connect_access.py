import hashlib
import time

from django.conf import settings
from django.core.cache.backends.filebased import FileBasedCache
from django.http import Http404
from django.utils.crypto import constant_time_compare


def access_key(name):
    if name != getattr(settings, "WHATSAPP_CONNECTION_NAME", "main"):
        raise Http404
    return f"wa_connect_auth_{name}"


def has_access(request, name):
    granted = request.session.get(access_key(name))
    # Legacy True grants and ordinary customer logins do not bypass this gate.
    return type(granted) in (int, float) and 0 <= time.time() - granted < 1800


def password_login(request, name):
    key = access_key(name)
    cache = FileBasedCache(str(settings.BASE_DIR / "logs" / "whatsapp_access_cache"), {})
    address = request.META.get("REMOTE_ADDR", "unknown")
    bucket = "wa-password-" + hashlib.sha256(address.encode()).hexdigest()
    attempts = cache.get(bucket, 0)
    if attempts >= 5:
        return "Demasiados intentos. Espera cinco minutos."
    entered = request.POST.get("pin", "")
    expected = str(getattr(settings, "WHATSAPP_CONNECT_PIN", "1234"))
    if constant_time_compare(entered, expected):
        request.session.cycle_key()
        request.session[key] = time.time()
        cache.delete(bucket)
        return ""
    cache.set(bucket, attempts + 1, timeout=300)
    return "Contraseña incorrecta."
