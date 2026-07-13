from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone

from . import bridge
from .models import WhatsAppConnection, WhatsAppLoginLink


@login_required
def whatsapp_connect(request, name):
    connection, _ = WhatsAppConnection.objects.get_or_create(name=name)

    qr_image = ""
    bridge_error = ""
    try:
        data = bridge.get_qr(connection)
        qr_image = data.get("qr_image", "")
        status = data.get("status", connection.status)
        phone = data.get("phone", connection.phone or "")
        connection.status = status
        connection.phone = phone
        connection.last_error = ""
        connection.save(update_fields=["status", "phone", "last_error", "updated_at"])
    except bridge.WhatsAppBridgeError as exc:
        bridge_error = str(exc)
        connection.status = WhatsAppConnection.Statuses.ERROR
        connection.last_error = bridge_error
        connection.save(update_fields=["status", "last_error", "updated_at"])

    return render(request, "whatsapp_bot/connect.html", {
        "connection": connection,
        "qr_image": qr_image,
        "bridge_error": bridge_error,
        "now": timezone.now(),
    })


# Keep old token-based view for backwards compatibility
def login_link(request, token):
    try:
        login = WhatsAppLoginLink.objects.select_related("connection").get(token=token)
    except WhatsAppLoginLink.DoesNotExist as exc:
        raise Http404 from exc

    qr_payload = None
    bridge_error = ""
    if login.is_valid:
        try:
            qr_payload = bridge.get_qr(login.connection)
            login.connection.status = login.connection.Statuses.QR_PENDING
            login.connection.last_error = ""
            login.connection.save(update_fields=["status", "last_error", "updated_at"])
        except bridge.WhatsAppBridgeError as exc:
            bridge_error = str(exc)
            login.connection.status = login.connection.Statuses.ERROR
            login.connection.last_error = bridge_error
            login.connection.save(update_fields=["status", "last_error", "updated_at"])

    if request.method == "POST" and login.is_valid:
        login.mark_used()

    return render(request, "whatsapp_bot/login_link.html", {
        "login": login,
        "connection": login.connection,
        "qr_payload": qr_payload or {},
        "bridge_error": bridge_error,
        "now": timezone.now(),
    })
