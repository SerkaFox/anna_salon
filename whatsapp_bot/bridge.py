import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


class WhatsAppBridgeError(Exception):
    pass


class WhatsAppNumberNotFound(WhatsAppBridgeError):
    """The destination number is not registered on WhatsApp."""


def _bridge_url(path):
    base_url = getattr(settings, "WHATSAPP_BRIDGE_URL", "").rstrip("/")
    if not base_url:
        raise WhatsAppBridgeError("WHATSAPP_BRIDGE_URL is not configured.")
    return f"{base_url}{path}"


def _request(path, payload=None, *, timeout=15):
    data = None
    headers = {"Accept": "application/json"}
    token = getattr(settings, "WHATSAPP_BRIDGE_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(_bridge_url(path), data=data, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if exc.code == 422:
            raise WhatsAppNumberNotFound(detail) from exc
        raise WhatsAppBridgeError(f"Bridge HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise WhatsAppBridgeError(f"Bridge unavailable: {exc.reason}") from exc
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WhatsAppBridgeError("Bridge returned invalid JSON.") from exc


def get_qr(connection):
    return _request("/auth/qr", {"session": connection.name})


def get_status(connection):
    return _request(f"/sessions/{connection.name}/status")


def reset_session(connection):
    return _request(f"/sessions/{connection.name}/reset", {})


def send_message(connection, *, to_phone, body):
    return _request(
        "/messages",
        {
            "session": connection.name,
            "to": to_phone,
            "body": body,
        },
        timeout=30,
    )


def send_buttons_message(connection, *, to_phone, body, buttons, title="", footer=""):
    """Send an interactive button message. buttons is a list of {"id": str, "body": str}."""
    return _request(
        "/messages/buttons",
        {
            "session": connection.name,
            "to": to_phone,
            "body": body,
            "buttons": buttons,
            "title": title,
            "footer": footer,
        },
        timeout=30,
    )
