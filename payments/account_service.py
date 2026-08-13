from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import stripe
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import StripePayoutRequest


def _money(cents):
    return (Decimal(cents or 0) / Decimal("100")).quantize(Decimal("0.01"))


def _amounts(items):
    return [
        {
            "amount": str(_money(item.amount)),
            "currency": item.currency.lower(),
            "source_types": dict(getattr(item, "source_types", {}) or {}),
        }
        for item in (items or [])
    ]


def _currency_amount(items, currency):
    currency = currency.lower()
    return next((_money(item.amount) for item in (items or []) if item.currency.lower() == currency), Decimal("0.00"))


def _stripe_error_message(exc):
    user_message = getattr(exc, "user_message", "")
    if user_message:
        return str(user_message)
    return "Stripe no pudo completar la operación. Revisa la cuenta de pagos."


def get_stripe_account_summary():
    if not getattr(settings, "STRIPE_SECRET_KEY", ""):
        return {"configured": False, "error": "Stripe no está configurado."}
    try:
        stripe.api_key = settings.STRIPE_SECRET_KEY
        balance = stripe.Balance.retrieve()
        account = stripe.Account.retrieve()
        destinations = []
        destinations_restricted = False
        try:
            external_accounts = stripe.Account.list_external_accounts(account.id, limit=10)
            for item in external_accounts.data:
                methods = list(getattr(item, "available_payout_methods", []) or [])
                destinations.append(
                    {
                        "id": item.id,
                        "type": item.object,
                        "label": (
                            f"Tarjeta terminada en {item.last4}"
                            if item.object == "card"
                            else f"Cuenta bancaria terminada en {item.last4}"
                        ),
                        "last4": item.last4,
                        "currency": item.currency.lower(),
                        "default": bool(getattr(item, "default_for_currency", False)),
                        "available_payout_methods": methods,
                    }
                )
        except stripe.PermissionError:
            destinations_restricted = True

        schedule = getattr(getattr(account, "settings", None), "payouts", None)
        schedule = getattr(schedule, "schedule", None)
        default_currency = account.default_currency.lower()
        available_amount = _currency_amount(balance.available, default_currency)
        pending_amount = _currency_amount(balance.pending, default_currency)
        instant_amount = _currency_amount(getattr(balance, "instant_available", []), default_currency)
        return {
            "configured": True,
            "error": "",
            "livemode": bool(balance.livemode),
            "country": account.country,
            "default_currency": default_currency,
            "available_amount": str(available_amount),
            "pending_amount": str(pending_amount),
            "instant_available_amount": str(instant_amount),
            "can_payout": bool(account.payouts_enabled and available_amount > 0),
            "can_instant_payout": bool(account.payouts_enabled and instant_amount > 0),
            "payouts_enabled": bool(account.payouts_enabled),
            "available": _amounts(balance.available),
            "pending": _amounts(balance.pending),
            "instant_available": _amounts(getattr(balance, "instant_available", [])),
            "schedule": {
                "interval": getattr(schedule, "interval", ""),
                "weekly_anchor": getattr(schedule, "weekly_anchor", ""),
                "monthly_anchor": getattr(schedule, "monthly_anchor", None),
            },
            "destinations": destinations,
            "destinations_restricted": destinations_restricted,
        }
    except stripe.StripeError as exc:
        return {"configured": True, "error": _stripe_error_message(exc)}


def create_stripe_payout(*, amount, currency="eur", method="standard", destination="", idempotency_key):
    if not getattr(settings, "STRIPE_SECRET_KEY", ""):
        raise ValidationError("Stripe no está configurado.")
    try:
        amount = Decimal(str(amount).replace(",", ".")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError("Importe no válido.") from exc
    if amount <= Decimal("0.00"):
        raise ValidationError("El importe debe ser mayor que cero.")
    currency = str(currency or "eur").lower()
    if currency != "eur":
        raise ValidationError("Solo están habilitadas las retiradas en EUR.")
    if method not in {"standard", "instant"}:
        raise ValidationError("Método de retirada no válido.")

    try:
        stripe.api_key = settings.STRIPE_SECRET_KEY
        balance = stripe.Balance.retrieve()
        source = balance.available if method == "standard" else getattr(balance, "instant_available", [])
        maximum = _currency_amount(source, currency)
        if amount > maximum:
            label = "instantáneamente" if method == "instant" else "para retirar"
            raise ValidationError(f"Saldo disponible {label}: {maximum:.2f} EUR.")
        payload = {
            "amount": int(amount * 100),
            "currency": currency,
            "method": method,
            "description": "Retirada BRIMOON Studio",
            "metadata": {"source": "anna_admin"},
        }
        if destination:
            payload["destination"] = destination
        payout = stripe.Payout.create(**payload, idempotency_key=str(idempotency_key))
        arrival_date = None
        if getattr(payout, "arrival_date", None):
            arrival_date = datetime.fromtimestamp(payout.arrival_date, tz=timezone.get_current_timezone()).date()
        return {
            "id": payout.id,
            "status": payout.status,
            "amount": str(_money(payout.amount)),
            "currency": payout.currency.lower(),
            "method": payout.method,
            "arrival_date": arrival_date,
        }
    except ValidationError:
        raise
    except stripe.StripeError as exc:
        raise ValidationError(_stripe_error_message(exc)) from exc


def request_stripe_payout(*, user, amount, currency="eur", method="standard", destination="", idempotency_key):
    payout_request, created = StripePayoutRequest.objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults={
            "requested_by": user,
            "amount": amount,
            "currency": currency,
            "method": method,
            "destination": destination,
        },
    )
    if not created:
        if payout_request.requested_by_id != user.pk:
            raise ValidationError("Solicitud de retirada no válida.")
        return payout_request, False
    try:
        result = create_stripe_payout(
            amount=amount,
            currency=currency,
            method=method,
            destination=destination,
            idempotency_key=idempotency_key,
        )
    except ValidationError as exc:
        payout_request.status = StripePayoutRequest.Statuses.FAILED
        payout_request.error = "; ".join(exc.messages)
        payout_request.save(update_fields=["status", "error", "updated_at"])
        raise
    status_value = result["status"]
    if status_value == "canceled":
        status_value = StripePayoutRequest.Statuses.CANCELLED
    elif status_value not in StripePayoutRequest.Statuses.values:
        status_value = StripePayoutRequest.Statuses.PENDING
    payout_request.stripe_payout_id = result["id"]
    payout_request.status = status_value
    payout_request.arrival_date = result["arrival_date"]
    payout_request.error = ""
    payout_request.save(
        update_fields=["stripe_payout_id", "status", "arrival_date", "error", "updated_at"]
    )
    return payout_request, True
