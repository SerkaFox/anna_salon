import logging
import time

from django.conf import settings
from django.core.cache.backends.filebased import FileBasedCache

logger = logging.getLogger(__name__)


def send_owner_alert(result):
    from mobile_api.models import PushDevice
    from mobile_api.push_notifications import _firebase_app

    app = _firebase_app()
    if app is None:
        logger.error("WhatsApp outage alert unavailable: Firebase is not configured.")
        return 0
    from firebase_admin import messaging

    sent = 0
    devices = PushDevice.objects.filter(is_active=True, user__is_active=True, user__role="owner")
    for device in devices:
        russian = device.locale == "ru"
        title = "WhatsApp требует внимания" if russian else "WhatsApp necesita atención"
        body = (
            "Сообщения клиентам приостановлены. Откройте защищённую страницу подключения WhatsApp."
            if russian else
            "Los mensajes a clientes están pausados. Abre la página protegida para vincular WhatsApp."
        )
        try:
            messaging.send(messaging.Message(
                token=device.registration_token,
                notification=messaging.Notification(title=title, body=body),
                data={"type": "whatsapp_connection", "url": result["reconnect_url"]},
                android=messaging.AndroidConfig(priority="high", notification=messaging.AndroidNotification(
                    channel_id="bookings", sound="default",
                )),
            ), app=app)
            sent += 1
        except Exception:
            logger.exception("WhatsApp outage alert failed for device %s", device.pk)
    return sent


def check_connection_alert(result):
    cache = FileBasedCache(str(settings.BASE_DIR / "logs" / "whatsapp_monitor_cache"), {})
    key = "outage-main"
    if result["connected"]:
        cache.delete(key)
        return 0
    now = time.time()
    incident = cache.get(key) or {"since": now, "last_attempt": 0, "last_sent": 0}
    sent = 0
    if (now - incident["since"] >= 300 and now - incident["last_attempt"] >= 300
            and now - incident["last_sent"] >= 3600):
        incident["last_attempt"] = now
        logger.error("BRIMOON WhatsApp outage: status=%s; reconnect=%s", result["status"], result["reconnect_url"])
        sent = send_owner_alert(result)
        if sent:
            incident["last_sent"] = now
    cache.set(key, incident, timeout=86400)
    return sent
