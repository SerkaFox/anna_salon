import json
import logging

from django.conf import settings
from django.contrib.auth import authenticate, login
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import bridge
from .models import WhatsAppConnection, WhatsAppLoginLink, WhatsAppMessage

logger = logging.getLogger(__name__)


def whatsapp_connect(request, name):
    login_error = ""
    pin = getattr(settings, "WHATSAPP_CONNECT_PIN", "1234")
    session_key = f"wa_connect_auth_{name}"

    # PIN login form submission
    if request.method == "POST" and not request.session.get(session_key):
        entered = request.POST.get("pin", "").strip()
        if entered == pin:
            request.session[session_key] = True
            return redirect(request.path)
        else:
            login_error = "PIN incorrecto."

    # Show PIN form if not authenticated via session or Django
    if not request.session.get(session_key) and not request.user.is_authenticated:
        return render(request, "whatsapp_bot/connect.html", {
            "show_login": True,
            "login_error": login_error,
            "name": name,
        })

    connection, _ = WhatsAppConnection.objects.get_or_create(name=name)

    has_qr = False
    bridge_error = ""
    try:
        data = bridge.get_qr(connection)
        has_qr = bool(data.get("qr", ""))
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
        "has_qr": has_qr,
        "bridge_error": bridge_error,
        "name": name,
        "now": timezone.now(),
    })


def whatsapp_qr_image(request, name):
    """Return the current QR code as a PNG image."""
    pin = getattr(settings, "WHATSAPP_CONNECT_PIN", "1234")
    session_key = f"wa_connect_auth_{name}"
    if not request.session.get(session_key) and not request.user.is_authenticated:
        return HttpResponse(status=403)

    try:
        data = bridge.get_qr(WhatsAppConnection.objects.get_or_create(name=name)[0])
    except bridge.WhatsAppBridgeError:
        return HttpResponse(status=503)

    raw_qr = data.get("qr", "")
    if not raw_qr:
        return HttpResponse(status=204)

    import io
    import qrcode
    img = qrcode.make(raw_qr)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return HttpResponse(buf.getvalue(), content_type="image/png")


@csrf_exempt
@require_POST
def button_reply_webhook(request):
    """Receives button-tap replies from the WhatsApp bridge and processes booking responses."""
    expected_token = getattr(settings, "WHATSAPP_BRIDGE_TOKEN", "")
    if expected_token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header != f"Bearer {expected_token}":
            return HttpResponse(status=401)

    try:
        payload = json.loads(request.body)
    except (ValueError, KeyError):
        return JsonResponse({"error": "invalid json"}, status=400)

    button_id = str(payload.get("button_id", ""))
    from_phone = str(payload.get("from_phone", "")).strip().lstrip("+")

    if not button_id or not from_phone:
        return JsonResponse({"ok": False, "reason": "missing fields"})

    # button_id format: attend_{booking_pk} or decline_{booking_pk}
    parts = button_id.split("_", 1)
    if len(parts) != 2 or parts[0] not in ("attend", "decline"):
        return JsonResponse({"ok": False, "reason": "unknown button"})

    action_key, booking_pk_str = parts
    try:
        booking_pk = int(booking_pk_str)
    except ValueError:
        return JsonResponse({"ok": False, "reason": "invalid booking id"})

    from bookings.models import Booking
    from bookings.client_actions import cancel_booking, booking_paid_amount, booking_amount_due
    from auditlog.services import log_event

    try:
        with transaction.atomic():
            booking = (
                Booking.objects.select_for_update()
                .select_related("client", "service", "employee")
                .prefetch_related("online_payments", "payments", "prepayment")
                .get(pk=booking_pk)
            )
    except Booking.DoesNotExist:
        return JsonResponse({"ok": False, "reason": "booking not found"})

    # Verify phone matches the client
    from .services import normalize_whatsapp_phone
    client_phone = normalize_whatsapp_phone(booking.client.phone).lstrip("+")
    if client_phone != from_phone:
        logger.warning("Button reply phone mismatch: expected %s got %s for booking %s", client_phone, from_phone, booking_pk)
        return JsonResponse({"ok": False, "reason": "phone mismatch"})

    if booking.status in {Booking.Statuses.CANCELLED, Booking.Statuses.DONE, Booking.Statuses.NO_SHOW}:
        return JsonResponse({"ok": True, "reason": "booking already closed"})

    if action_key == "decline":
        booking.client_response = Booking.ClientResponses.DECLINED
        booking.client_responded_at = timezone.now()
        booking.save(update_fields=["client_response", "client_responded_at", "updated_at"])
        cancel_booking(booking, force_refund=True)
        from .services import queue_and_send
        queue_and_send(booking, kind=WhatsAppMessage.Kinds.BOOKING_CANCELLED)
        log_event(actor=None, section="booking", action="client_declined", instance=booking,
                  message=f"Cliente indicó por WhatsApp que no asistirá a la reserva #{booking.pk}.")
        return JsonResponse({"ok": True, "action": "declined"})

    # attending
    booking.client_response = Booking.ClientResponses.ATTENDING
    booking.client_responded_at = timezone.now()
    if booking.status == Booking.Statuses.PENDING:
        booking.status = Booking.Statuses.CONFIRMED
        booking.save(update_fields=["client_response", "client_responded_at", "status", "updated_at"])
    else:
        booking.save(update_fields=["client_response", "client_responded_at", "updated_at"])
    log_event(actor=None, section="booking", action="client_attending", instance=booking,
              message=f"Cliente confirmó asistencia por WhatsApp a la reserva #{booking.pk}.")

    # Check if deposit is due — send payment link via WhatsApp
    from bookings.services import get_booking_deposit_amount
    deposit_due = min(
        max(get_booking_deposit_amount(booking) - booking_paid_amount(booking), 0),
        booking_amount_due(booking),
    )
    if deposit_due > 0 and not getattr(settings, "DEMO_MODE", False):
        try:
            from payments.stripe_service import create_checkout_session, create_pending_stripe_payment
            payment = create_pending_stripe_payment(booking, amount=deposit_due, reason="booking_deposit_payment")
            create_checkout_session(payment, request)
            base_url = getattr(settings, "PUBLIC_BASE_URL", "").rstrip("/")
            pay_url = payment.checkout_url or f"{base_url}/bookings/{booking.pk}/pay/"
            from .services import send_whatsapp_message, get_default_connection, normalize_whatsapp_phone
            phone = normalize_whatsapp_phone(booking.client.phone)
            from django.utils import timezone as tz
            WhatsAppMessage.objects.create(
                connection=get_default_connection(),
                booking=booking,
                client=booking.client,
                kind=WhatsAppMessage.Kinds.BOOKING_CONFIRMATION,
                to_phone=phone,
                body=(
                    f"¡Perfecto! Para confirmar tu cita, paga la señal de {deposit_due:.0f} € en los "
                    f"próximos 30 minutos:\n💳 {pay_url}"
                ),
                scheduled_for=tz.now(),
            )
        except Exception:
            logger.exception("Could not create deposit payment for booking %s after button reply.", booking_pk)

    return JsonResponse({"ok": True, "action": "attending"})


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
