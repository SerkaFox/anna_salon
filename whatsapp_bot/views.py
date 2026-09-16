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
from django.views.decorators.cache import never_cache

from . import bridge
from .models import WhatsAppConnection, WhatsAppLoginLink, WhatsAppMessage
from .connect_access import has_access, password_login

logger = logging.getLogger(__name__)


@never_cache
def whatsapp_connect(request, name):
    login_error = ""

    # PIN login form submission
    if request.method == "POST" and not has_access(request, name):
        login_error = password_login(request, name)
        if not login_error:
            return redirect(request.path)

    # Show PIN form if not authenticated via session or Django
    if not has_access(request, name):
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
        connection.status = (
            WhatsAppConnection.Statuses.CONNECTED if status == "ready"
            else WhatsAppConnection.Statuses.QR_PENDING if status in {"qr", "pairing", "starting", "authenticated"}
            else WhatsAppConnection.Statuses.ERROR
        )
        connection.phone = phone
        connection.last_error = str(data.get("error") or "")
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
        "bridge_status": status if not bridge_error else "error",
        "auth_mode": data.get("auth_mode", "qr") if not bridge_error else "qr",
    })


@never_cache
def whatsapp_pairing_code(request, name):
    """Request a WhatsApp pairing code (no-QR alternative)."""
    if not has_access(request, name):
        return redirect("whatsapp_bot:connect", name=name)

    connection, _ = WhatsAppConnection.objects.get_or_create(name=name)
    pairing_code = None
    error = ""
    phone = connection.phone or ""

    if request.method == "POST":
        phone = request.POST.get("phone", "").strip().replace(" ", "").replace("+", "")
        try:
            result = bridge.request_pairing_code(connection, phone)
            pairing_code = result.get("code") or result.get("note")
        except bridge.WhatsAppBridgeError as exc:
            logger.warning("WhatsApp pairing failed for %s: %s", name, exc)
            error = "WhatsApp no pudo generar el código. Espera un minuto y vuelve a intentarlo, o utiliza el QR. No se ha borrado el acceso guardado."

    if request.method == "GET":
        try:
            progress = bridge.pairing_progress(connection)
            if progress.get("status") == "ready":
                return redirect("whatsapp_bot:connect", name=name)
            pairing_code = progress.get("code")
        except bridge.WhatsAppBridgeError:
            pass

    return render(request, "whatsapp_bot/pairing_code.html", {
        "name": name,
        "connection": connection,
        "pairing_code": pairing_code,
        "phone": phone,
        "error": error,
        "now": timezone.now(),
    })


@never_cache
def whatsapp_pairing_progress(request, name):
    if not has_access(request, name):
        return JsonResponse({"error": "access_expired"}, status=403)
    try:
        data = bridge.pairing_progress(WhatsAppConnection.objects.get_or_create(name=name)[0])
    except bridge.WhatsAppBridgeError:
        return JsonResponse({"error": "bridge_unavailable"}, status=503)
    return JsonResponse({k: data.get(k) for k in ("status", "auth_mode", "code", "code_at")})


@never_cache
def whatsapp_qr_image(request, name):
    """Return the current QR code as a PNG image."""
    if not has_access(request, name):
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


@never_cache
@require_POST
def whatsapp_return_to_qr(request, name):
    if not has_access(request, name):
        return redirect("whatsapp_bot:connect", name=name)
    try:
        bridge.cancel_pairing(WhatsAppConnection.objects.get_or_create(name=name)[0])
    except bridge.WhatsAppBridgeError as exc:
        logger.warning("WhatsApp return to QR failed for %s: %s", name, exc)
    return redirect("whatsapp_bot:connect", name=name)


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

    action_key = None
    booking_pk_str = ""
    for prefix, action in (
        ("confirm_decline_", "confirm_decline"),
        ("keep_booking_", "keep_booking"),
        ("attend_", "attend"),
        ("decline_", "decline"),
    ):
        if button_id.startswith(prefix):
            action_key = action
            booking_pk_str = button_id[len(prefix):]
            break
    if action_key is None:
        return JsonResponse({"ok": False, "reason": "unknown button"})
    try:
        booking_pk = int(booking_pk_str)
    except ValueError:
        return JsonResponse({"ok": False, "reason": "invalid booking id"})

    from bookings.models import Booking
    from bookings.client_actions import cancel_booking, booking_paid_amount, booking_amount_due
    from bookings.utils import exact_duplicate_bookings
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

    if (
        booking.status
        in {Booking.Statuses.CANCELLED, Booking.Statuses.DONE, Booking.Statuses.NO_SHOW}
        and action_key not in {"decline", "confirm_decline"}
    ):
        return JsonResponse({"ok": True, "reason": "booking already closed"})

    if (
        booking.client_response == Booking.ClientResponses.ATTENDING
        and action_key in {"attend", "decline"}
    ):
        return JsonResponse({"ok": True, "reason": "response already recorded"})

    if action_key == "keep_booking" and (
        booking.client_response != Booking.ClientResponses.CANCELLATION_PENDING
    ):
        return JsonResponse({"ok": False, "reason": "cancellation confirmation not requested"}, status=409)

    # A written negative reply is itself the client's explicit confirmation.
    if action_key == "decline":
        action_key = "confirm_decline"

    if action_key == "keep_booking":
        booking.client_response = Booking.ClientResponses.ATTENDING
        booking.client_responded_at = timezone.now()
        booking.save(update_fields=["client_response", "client_responded_at", "updated_at"])
        from .services import send_booking_kept_confirmation
        try:
            send_booking_kept_confirmation(booking)
        except Exception:
            logger.exception("Could not send booking-kept confirmation for booking %s.", booking.pk)
        log_event(
            actor=None,
            section="booking",
            action="cancellation_aborted",
            instance=booking,
            message=f"Cliente decidió mantener la reserva #{booking.pk} por WhatsApp.",
        )
        return JsonResponse({"ok": True, "action": "booking_kept"})

    if action_key == "confirm_decline":
        with transaction.atomic():
            duplicate_group = list(
                exact_duplicate_bookings(booking)
                .select_for_update()
                .exclude(
                    status__in={
                        Booking.Statuses.CANCELLED,
                        Booking.Statuses.DONE,
                        Booking.Statuses.NO_SHOW,
                    }
                )
            )
            if not duplicate_group:
                return JsonResponse({"ok": True, "reason": "booking already closed"})
            responded_at = timezone.now()
            for duplicate in duplicate_group:
                duplicate.client_response = Booking.ClientResponses.DECLINED
                duplicate.client_responded_at = responded_at
                duplicate.save(
                    update_fields=[
                        "client_response",
                        "client_responded_at",
                        "updated_at",
                    ]
                )
                cancel_booking(duplicate, force_refund=True)
                log_event(
                    actor=None,
                    section="booking",
                    action="client_declined",
                    instance=duplicate,
                    message=(
                        f"Cliente confirmó por WhatsApp que no asistirá a la "
                        f"reserva #{duplicate.pk}."
                    ),
                    metadata={
                        "duplicate_group": [item.pk for item in duplicate_group]
                    },
                )
        from .services import queue_and_send
        queue_and_send(booking, kind=WhatsAppMessage.Kinds.BOOKING_CANCELLED)
        return JsonResponse(
            {
                "ok": True,
                "action": "declined_confirmed",
                "cancelled_booking_ids": [item.pk for item in duplicate_group],
            }
        )

    if (
        action_key == "attend"
        and booking.client_response == Booking.ClientResponses.CANCELLATION_PENDING
    ):
        booking.client_response = Booking.ClientResponses.ATTENDING
        booking.client_responded_at = timezone.now()
        booking.save(update_fields=["client_response", "client_responded_at", "updated_at"])
        from .services import send_booking_kept_confirmation
        try:
            send_booking_kept_confirmation(booking)
        except Exception:
            logger.exception("Could not send booking-kept confirmation for booking %s.", booking.pk)
        log_event(
            actor=None,
            section="booking",
            action="cancellation_aborted",
            instance=booking,
            message=f"Cliente decidió mantener la reserva #{booking.pk} por WhatsApp.",
        )
        return JsonResponse({"ok": True, "action": "booking_kept"})

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
@never_cache
def login_link(request, token):
    try:
        login_obj = WhatsAppLoginLink.objects.select_related("connection").get(token=token)
    except WhatsAppLoginLink.DoesNotExist as exc:
        raise Http404 from exc

    if not has_access(request, login_obj.connection.name):
        return redirect("whatsapp_bot:connect", name=login_obj.connection.name)

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
