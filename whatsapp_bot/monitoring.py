from django.conf import settings
from django.utils import timezone

from . import bridge
from .models import WhatsAppConnection


UNHEALTHY_BRIDGE_STATUSES = {
    "qr",
    "qr_pending",
    "auth_failure",
    "disconnected",
    "unpaired",
    "unpaired_idle",
    "error",
}


def refresh_connection_status(name="main"):
    """Read the bridge itself and persist a truthful, user-facing status."""
    connection, _ = WhatsAppConnection.objects.get_or_create(
        name=name,
        defaults={"bridge_session_id": name},
    )
    checked_at = timezone.now()
    raw_status = "error"
    phone = connection.phone
    error = ""

    try:
        result = bridge.get_status(connection)
        raw_status = str(
            result.get("wa_state") or result.get("status") or "error"
        ).strip().lower()
        phone = str(result.get("phone") or phone or "").strip()
        if raw_status in {"ready", "connected"}:
            stored_status = WhatsAppConnection.Statuses.CONNECTED
        elif raw_status in {"qr", "qr_pending", "starting", "initializing"}:
            stored_status = WhatsAppConnection.Statuses.QR_PENDING
        elif raw_status in {"disconnected", "unpaired", "unpaired_idle"}:
            stored_status = WhatsAppConnection.Statuses.DISCONNECTED
        else:
            stored_status = WhatsAppConnection.Statuses.ERROR
            error = str(result.get("error") or f"Bridge status: {raw_status}")
    except bridge.WhatsAppBridgeError as exc:
        stored_status = WhatsAppConnection.Statuses.ERROR
        error = str(exc)

    connection.status = stored_status
    connection.phone = phone
    connection.last_error = error
    connection.last_seen_at = checked_at
    connection.save(
        update_fields=["status", "phone", "last_error", "last_seen_at", "updated_at"]
    )

    connected = stored_status == WhatsAppConnection.Statuses.CONNECTED
    base_url = getattr(settings, "PUBLIC_BASE_URL", "").rstrip("/")
    return {
        "status": raw_status,
        "connected": connected,
        "needs_reconnect": not connected,
        "phone": phone,
        "error": error,
        "last_checked_at": checked_at.isoformat(),
        "reconnect_url": f"{base_url}/whatsapp/connect/{connection.name}/",
    }
