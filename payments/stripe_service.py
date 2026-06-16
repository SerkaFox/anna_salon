import json
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import stripe
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from bookings.models import Booking
from bookings.services import create_booking_prepayment

from .models import Payment, PaymentRefund


def _decimal_setting(value):
    if value in {None, ""}:
        return None
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError("Importe de depósito Stripe inválido.") from exc
    return amount if amount > Decimal("0.00") else None


def get_booking_checkout_amount(booking):
    deposit_amount = _decimal_setting(getattr(settings, "BOOKING_DEPOSIT_AMOUNT_EUR", ""))
    if deposit_amount is not None:
        return deposit_amount
    amount = booking.client_price_snapshot or booking.price_snapshot or Decimal("0.00")
    return Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def create_pending_stripe_payment(booking, amount=None, *, status=None, reason="booking_payment"):
    amount = amount if amount is not None else get_booking_checkout_amount(booking)
    amount = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount <= Decimal("0.00"):
        raise ValidationError("La reserva no tiene importe para pagar.")
    return Payment.objects.create(
        booking=booking,
        amount=amount,
        currency=getattr(settings, "STRIPE_CURRENCY", "eur").lower(),
        order_number=f"stripe-{uuid.uuid4().hex}",
        provider=Payment.Providers.STRIPE,
        method=Payment.Methods.CARD,
        status=status or Payment.Statuses.PENDING,
        raw_request={"reason": reason},
    )


def create_checkout_session(payment, request):
    if not getattr(settings, "STRIPE_SECRET_KEY", ""):
        raise ValidationError("Stripe no está configurado.")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    booking = payment.booking
    amount_cents = int((payment.amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    success_url = request.build_absolute_uri(reverse("payments:stripe_success"))
    cancel_url = request.build_absolute_uri(reverse("payments:stripe_cancel"))
    customer_email = booking.client.email or getattr(getattr(booking.client, "user", None), "email", "") or ""
    description = f"{booking.service.name} · {timezone.localtime(booking.start_at):%d/%m/%Y %H:%M}"
    session = stripe.checkout.Session.create(
        mode="payment",
        payment_method_types=["card"],
        line_items=[
            {
                "price_data": {
                    "currency": payment.currency,
                    "product_data": {
                        "name": f"Reserva BRIMOON Studio #{booking.pk}",
                        "description": description[:500],
                    },
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }
        ],
        success_url=f"{success_url}?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=cancel_url,
        customer_email=customer_email or None,
        client_reference_id=str(booking.pk),
        metadata={
            "payment_id": str(payment.pk),
            "booking_id": str(booking.pk),
            "provider": Payment.Providers.STRIPE,
        },
    )
    payment.stripe_checkout_session_id = session.id
    payment.stripe_payment_intent_id = getattr(session, "payment_intent", "") or ""
    payment.stripe_customer_email = customer_email
    payment.checkout_url = session.url
    payment.raw_request = {
        "provider": Payment.Providers.STRIPE,
        "checkout_session_id": session.id,
        "amount_cents": amount_cents,
        "currency": payment.currency,
    }
    payment.save(
        update_fields=[
            "stripe_checkout_session_id",
            "stripe_payment_intent_id",
            "stripe_customer_email",
            "checkout_url",
            "raw_request",
            "updated_at",
        ]
    )
    return session


def verify_webhook_signature(payload, sig_header):
    if not getattr(settings, "STRIPE_WEBHOOK_SECRET", ""):
        raise ValidationError("Webhook Stripe no configurado.")
    return stripe.Webhook.construct_event(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET)


def _stripe_plain(value):
    if hasattr(value, "to_dict_recursive"):
        value = value.to_dict_recursive()
    elif hasattr(value, "to_dict"):
        value = value.to_dict()
    if isinstance(value, dict):
        return {key: _stripe_plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_stripe_plain(item) for item in value]
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value


def safe_stripe_event_payload(event):
    event_object = _event_object(event)
    return {
        "id": _object_get(event, "id", ""),
        "type": _event_type(event),
        "livemode": bool(_object_get(event, "livemode", False)),
        "created": _object_get(event, "created", None),
        "data": _stripe_plain(event_object),
    }


def _event_type(event):
    return event.get("type") if isinstance(event, dict) else getattr(event, "type", "")


def _event_object(event):
    if isinstance(event, dict):
        return event.get("data", {}).get("object", {})
    return getattr(getattr(event, "data", None), "object", {})


def _object_get(obj, key, default=""):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _metadata_get(obj, key):
    metadata = _object_get(obj, "metadata", {}) or {}
    return metadata.get(key, "") if isinstance(metadata, dict) else getattr(metadata, key, "")


def _find_payment_from_session(session):
    payment_id = _metadata_get(session, "payment_id")
    queryset = Payment.objects.select_for_update().select_related("booking")
    if payment_id:
        try:
            return queryset.get(pk=payment_id, provider=Payment.Providers.STRIPE)
        except (Payment.DoesNotExist, ValueError):
            pass
    session_id = _object_get(session, "id", "")
    return queryset.get(stripe_checkout_session_id=session_id, provider=Payment.Providers.STRIPE)


def _find_payment_from_intent(intent):
    intent_id = _object_get(intent, "id", "")
    return (
        Payment.objects.select_for_update()
        .select_related("booking")
        .filter(stripe_payment_intent_id=intent_id, provider=Payment.Providers.STRIPE)
        .first()
    )


def _mark_paid(payment, event, *, session=None, intent=None):
    if session is not None:
        payment.stripe_checkout_session_id = _object_get(session, "id", payment.stripe_checkout_session_id) or payment.stripe_checkout_session_id
        payment.stripe_payment_intent_id = _object_get(session, "payment_intent", payment.stripe_payment_intent_id) or payment.stripe_payment_intent_id
        customer_details = _object_get(session, "customer_details", {}) or {}
        email = _object_get(customer_details, "email", "") if not isinstance(customer_details, dict) else customer_details.get("email", "")
        payment.stripe_customer_email = email or payment.stripe_customer_email
    if intent is not None:
        payment.stripe_payment_intent_id = _object_get(intent, "id", payment.stripe_payment_intent_id) or payment.stripe_payment_intent_id
    payment.status = Payment.Statuses.PAID
    if payment.paid_at is None:
        payment.paid_at = timezone.now()
    payment.raw_event = safe_stripe_event_payload(event)
    payment.save(
        update_fields=[
            "status",
            "paid_at",
            "stripe_checkout_session_id",
            "stripe_payment_intent_id",
            "stripe_customer_email",
            "raw_event",
            "updated_at",
        ]
    )
    booking = payment.booking
    if booking.status == Booking.Statuses.PENDING:
        booking.status = Booking.Statuses.CONFIRMED
        booking.save(update_fields=["status", "updated_at"])
    create_booking_prepayment(booking, payment)
    return payment


def handle_checkout_session_completed(event):
    session = _event_object(event)
    with transaction.atomic():
        payment = _find_payment_from_session(session)
        return _mark_paid(payment, event, session=session)


def handle_checkout_session_expired(event):
    session = _event_object(event)
    with transaction.atomic():
        payment = _find_payment_from_session(session)
        if payment.status != Payment.Statuses.PAID:
            payment.status = Payment.Statuses.EXPIRED
            payment.raw_event = safe_stripe_event_payload(event)
            payment.save(update_fields=["status", "raw_event", "updated_at"])
        return payment


def handle_payment_intent_succeeded(event):
    intent = _event_object(event)
    with transaction.atomic():
        payment = _find_payment_from_intent(intent)
        if payment is None:
            return None
        return _mark_paid(payment, event, intent=intent)


def handle_payment_intent_failed(event):
    intent = _event_object(event)
    with transaction.atomic():
        payment = _find_payment_from_intent(intent)
        if payment is None:
            return None
        if payment.status != Payment.Statuses.PAID:
            payment.status = Payment.Statuses.FAILED
            payment.raw_event = safe_stripe_event_payload(event)
            payment.save(update_fields=["status", "raw_event", "updated_at"])
        return payment


def _refund_status_from_stripe(stripe_status):
    if stripe_status == "succeeded":
        return PaymentRefund.Statuses.REFUNDED
    if stripe_status == "failed":
        return PaymentRefund.Statuses.FAILED
    if stripe_status == "canceled":
        return PaymentRefund.Statuses.CANCELLED
    return PaymentRefund.Statuses.PENDING


def _sync_payment_refund_status(payment):
    refunded_total = sum(
        (refund.amount for refund in payment.refunds.filter(status=PaymentRefund.Statuses.REFUNDED)),
        Decimal("0.00"),
    )
    pending_exists = payment.refunds.filter(status=PaymentRefund.Statuses.PENDING).exists()
    payment.amount_refunded = refunded_total
    if pending_exists:
        payment.status = Payment.Statuses.REFUND_PENDING
    elif refunded_total >= payment.amount and refunded_total > Decimal("0.00"):
        payment.status = Payment.Statuses.REFUNDED
        payment.refunded_at = timezone.now()
    elif refunded_total > Decimal("0.00"):
        payment.status = Payment.Statuses.PARTIALLY_REFUNDED
    payment.save(update_fields=["amount_refunded", "status", "updated_at", "refunded_at"])


def create_refund(payment, amount=None, reason="requested_by_customer"):
    if payment.provider != Payment.Providers.STRIPE:
        raise ValidationError("Solo los pagos Stripe pueden devolverse automáticamente.")
    if not payment.stripe_payment_intent_id:
        raise ValidationError("El pago no tiene PaymentIntent de Stripe.")
    if not getattr(settings, "STRIPE_SECRET_KEY", ""):
        raise ValidationError("Stripe no está configurado.")
    amount = Decimal(amount if amount is not None else payment.refundable_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount <= Decimal("0.00"):
        raise ValidationError("No hay importe disponible para devolver.")
    if amount > payment.refundable_amount:
        raise ValidationError("El importe de devolución supera el importe disponible.")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    refund_data = stripe.Refund.create(
        payment_intent=payment.stripe_payment_intent_id,
        amount=int((amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
        reason=reason,
        metadata={"payment_id": str(payment.pk), "booking_id": str(payment.booking_id)},
    )
    plain = _stripe_plain(refund_data)
    refund = PaymentRefund.objects.create(
        payment=payment,
        amount=amount,
        status=_refund_status_from_stripe(_object_get(refund_data, "status", "")),
        stripe_refund_id=_object_get(refund_data, "id", ""),
        reason=reason,
        raw_response=plain,
        refunded_at=timezone.now() if _object_get(refund_data, "status", "") == "succeeded" else None,
    )
    payment.stripe_refund_id = refund.stripe_refund_id
    payment.refund_reason = reason
    payment.save(update_fields=["stripe_refund_id", "refund_reason", "updated_at"])
    _sync_payment_refund_status(payment)
    return refund


def _find_refund_from_event(refund_object):
    refund_id = _object_get(refund_object, "id", "")
    if not refund_id:
        return None
    return PaymentRefund.objects.select_related("payment").filter(stripe_refund_id=refund_id).first()


def handle_refund_updated(event):
    refund_object = _event_object(event)
    with transaction.atomic():
        refund = _find_refund_from_event(refund_object)
        if refund is None:
            return None
        refund.status = _refund_status_from_stripe(_object_get(refund_object, "status", ""))
        refund.raw_response = _stripe_plain(refund_object)
        if refund.status == PaymentRefund.Statuses.REFUNDED and refund.refunded_at is None:
            refund.refunded_at = timezone.now()
        refund.save(update_fields=["status", "raw_response", "refunded_at"])
        _sync_payment_refund_status(refund.payment)
        return refund


def handle_stripe_event(event):
    event_type = _event_type(event)
    if event_type == "checkout.session.completed":
        return handle_checkout_session_completed(event)
    if event_type == "checkout.session.expired":
        return handle_checkout_session_expired(event)
    if event_type == "payment_intent.succeeded":
        return handle_payment_intent_succeeded(event)
    if event_type == "payment_intent.payment_failed":
        return handle_payment_intent_failed(event)
    if event_type in {"refund.updated", "charge.refunded"}:
        return handle_refund_updated(event)
    return None
