from django.http import Http404
from django.shortcuts import render
from django.utils import timezone

from . import bridge
from .models import WhatsAppLoginLink


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

    return render(
        request,
        "whatsapp_bot/login_link.html",
        {
            "login": login,
            "connection": login.connection,
            "qr_payload": qr_payload or {},
            "bridge_error": bridge_error,
            "now": timezone.now(),
        },
    )
