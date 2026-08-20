import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.http import Http404, HttpResponseBadRequest, HttpResponse
import stripe

from accounts.permissions import get_client_profile
from bookings.models import Booking
from bookings.services import create_booking_prepayment
from .models import Payment
from .redsys import RedsysSignatureError, is_successful_response, sanitize_redsys_payload, verify_signature
from .stripe_service import handle_stripe_event, verify_webhook_signature


def public_payment(request, reference):
    """Resolve a compact BRIMOON payment URL to the active Stripe checkout."""
    payment = Payment.objects.filter(
        order_number=f"stripe-{reference}",
        provider=Payment.Providers.STRIPE,
    ).first()
    if payment is None:
        raise Http404
    if payment.status not in {
        Payment.Statuses.PENDING,
        Payment.Statuses.EXTRA_PAYMENT_PENDING,
    } or not payment.checkout_url:
        return render(
            request,
            "payments/stripe_result.html",
            {"status": "unavailable"},
        )
    return redirect(payment.checkout_url)


@csrf_exempt
@require_POST
def redsys_notification(request):
    encoded_parameters = request.POST.get("Ds_MerchantParameters", "")
    signature = request.POST.get("Ds_Signature", "")
    if not encoded_parameters or not signature:
        return HttpResponseBadRequest("Missing Redsys parameters.")

    try:
        payload = verify_signature(encoded_parameters, signature)
    except RedsysSignatureError:
        return HttpResponseBadRequest("Invalid Redsys signature.")
    except Exception:
        return HttpResponseBadRequest("Invalid Redsys payload.")

    order_number = payload.get("Ds_Order") or payload.get("DS_MERCHANT_ORDER")
    response_code = str(payload.get("Ds_Response", ""))
    authorisation_code = str(payload.get("Ds_AuthorisationCode", ""))
    safe_payload = sanitize_redsys_payload(payload)

    with transaction.atomic():
        try:
            payment = Payment.objects.select_for_update().get(order_number=order_number)
        except Payment.DoesNotExist:
            return HttpResponseBadRequest("Unknown Redsys order.")

        payment.raw_response = safe_payload
        payment.redsys_response_code = response_code
        payment.redsys_authorisation_code = authorisation_code
        if is_successful_response(response_code):
            payment.status = Payment.Statuses.PAID
            payment.method = Payment.Methods.CARD if payment.method == Payment.Methods.UNKNOWN else payment.method
            if payment.paid_at is None:
                payment.paid_at = timezone.now()
        elif payment.status != Payment.Statuses.PAID:
            payment.status = Payment.Statuses.FAILED
        payment.save(
            update_fields=[
                "raw_response",
                "redsys_response_code",
                "redsys_authorisation_code",
                "status",
                "method",
                "paid_at",
                "updated_at",
            ]
        )

    return HttpResponse("OK")


def redsys_success(request):
    return render(request, "payments/redsys_result.html", {"status": "success"})


def redsys_error(request):
    return render(request, "payments/redsys_result.html", {"status": "error"})


def stripe_success(request):
    session_id = request.GET.get("session_id", "")
    payments = []
    if session_id:
        payments = list(
            Payment.objects.select_related("booking", "booking__client")
            .filter(stripe_checkout_session_id=session_id, provider=Payment.Providers.STRIPE)
        )
    if payments:
        if len(payments) > 1:
            messages.success(request, f"Pago recibido para {len(payments)} reservas. En unos segundos Stripe confirmará el estado final.")
        else:
            messages.success(request, "Pago recibido. En unos segundos Stripe confirmará el estado final de la reserva.")
        client = getattr(request.user, "client_profile", None) if request.user.is_authenticated else None
        if client and len(payments) == 1 and payments[0].booking.client_id == client.pk:
            return redirect("clients:booking_detail", pk=payments[0].booking_id)
        return redirect("clients:portal")
    return render(request, "payments/stripe_result.html", {"status": "success"})


def stripe_cancel(request):
    session_id = request.GET.get("session_id", "")
    payments = []
    if session_id:
        payments = list(
            Payment.objects.select_related("booking", "booking__client")
            .filter(stripe_checkout_session_id=session_id, provider=Payment.Providers.STRIPE)
        )
    if payments:
        messages.info(request, "Pago cancelado. No se ha realizado ningún cargo.")
        client = getattr(request.user, "client_profile", None) if request.user.is_authenticated else None
        if client and len(payments) == 1 and payments[0].booking.client_id == client.pk:
            return redirect("clients:booking_detail", pk=payments[0].booking_id)
        if client:
            return redirect("clients:portal")
    return render(request, "payments/stripe_result.html", {"status": "cancel"})


@csrf_exempt
@require_POST
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    try:
        event = verify_webhook_signature(payload, sig_header)
    except (ValueError, ValidationError, stripe.error.SignatureVerificationError):
        return HttpResponseBadRequest("Invalid Stripe signature.")

    try:
        handle_stripe_event(event)
    except Payment.DoesNotExist:
        return HttpResponseBadRequest("Unknown Stripe payment.")

    return HttpResponse("OK")


@login_required
@require_POST
def demo_pay(request, pk):
    """Fake payment for DEMO_MODE — immediately marks booking as paid."""
    if not getattr(settings, "DEMO_MODE", False):
        raise PermissionDenied

    client = get_client_profile(request.user)
    if not client:
        raise PermissionDenied

    booking = get_object_or_404(
        Booking.objects.select_related("client", "service", "employee"),
        pk=pk,
        client=client,
    )
    if booking.status in {Booking.Statuses.CANCELLED, Booking.Statuses.NO_SHOW}:
        messages.error(request, "Esta reserva no se puede pagar.")
        return redirect("clients:booking_detail", pk=pk)

    from django.db.models import Sum as _Sum
    existing_paid = booking.online_payments.filter(
        status=Payment.Statuses.PAID
    ).aggregate(total=_Sum("amount"))["total"] or Decimal("0.00")

    amount = (booking.client_price_snapshot or Decimal("0.00")) - existing_paid
    if amount <= Decimal("0.00"):
        messages.success(request, "Esta reserva ya está pagada.")
        return redirect("clients:booking_detail", pk=pk)

    with transaction.atomic():
        payment = Payment.objects.create(
            booking=booking,
            amount=amount,
            currency="eur",
            order_number=f"demo-{uuid.uuid4().hex[:12]}",
            provider=Payment.Providers.STRIPE,
            method=Payment.Methods.CARD,
            status=Payment.Statuses.PAID,
            paid_at=timezone.now(),
            raw_request={"demo": True},
        )
        if booking.status == Booking.Statuses.PENDING:
            booking.status = Booking.Statuses.CONFIRMED
            booking.save(update_fields=["status", "updated_at"])
        create_booking_prepayment(booking, payment)

    try:
        from notifications.services import notify_payment_receipt
        notify_payment_receipt(booking, payment)
    except Exception:
        pass

    messages.success(request, f"✅ Pago de {amount:.2f} € recibido. ¡Tu reserva está confirmada!")
    return redirect("clients:booking_detail", pk=pk)
