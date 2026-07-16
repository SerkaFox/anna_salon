from django.contrib.auth import authenticate, login
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone

from . import bridge
from .models import WhatsAppConnection, WhatsAppLoginLink


def whatsapp_connect(request, name):
    login_error = ""

    # Handle login form submission
    if request.method == "POST" and not request.user.is_authenticated:
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user is not None and user.is_active:
            login(request, user)
            return redirect(request.path)
        else:
            login_error = "Usuario o contraseña incorrectos."

    # Show login form if not authenticated
    if not request.user.is_authenticated:
        return render(request, "whatsapp_bot/connect.html", {
            "show_login": True,
            "login_error": login_error,
            "name": name,
        })

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
        "show_login": False,
        "connection": connection,
        "qr_image": qr_image,
        "bridge_error": bridge_error,
        "now": timezone.now(),
    })


# Keep old token-based view for backwards compatibility
def login_link(request, token):
    try:
        login_obj = WhatsAppLoginLink.objects.select_related("connection").get(token=token)
    except WhatsAppLoginLink.DoesNotExist as exc:
        raise Http404 from exc

    qr_payload = None
    bridge_error = ""
    if login_obj.is_valid:
        try:
            qr_payload = bridge.get_qr(login_obj.connection)
            login_obj.connection.status = login_obj.connection.Statuses.QR_PENDING
            login_obj.connection.last_error = ""
            login_obj.connection.save(update_fields=["status", "last_error", "updated_at"])
        except bridge.WhatsAppBridgeError as exc:
            bridge_error = str(exc)
            login_obj.connection.status = login_obj.connection.Statuses.ERROR
            login_obj.connection.last_error = bridge_error
            login_obj.connection.save(update_fields=["status", "last_error", "updated_at"])

    if request.method == "POST" and login_obj.is_valid:
        login_obj.mark_used()

    return render(request, "whatsapp_bot/login_link.html", {
        "login": login_obj,
        "connection": login_obj.connection,
        "qr_payload": qr_payload or {},
        "bridge_error": bridge_error,
        "now": timezone.now(),
    })
