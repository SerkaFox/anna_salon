from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP

from django.conf import settings
from django.db import DatabaseError


def get_deposit_percent():
    from .models import SalonSettings

    try:
        return SalonSettings.load().deposit_percent
    except DatabaseError:
        try:
            return Decimal(str(getattr(settings, "BOOKING_DEPOSIT_PERCENT", "10")))
        except (InvalidOperation, TypeError, ValueError):
            return Decimal("10")


def get_deposit_settings():
    from .models import SalonSettings

    try:
        salon_settings = SalonSettings.load()
        return {
            "percent": salon_settings.deposit_percent,
            "minimum_amount": salon_settings.deposit_minimum_amount,
            "rounding": salon_settings.deposit_rounding,
        }
    except DatabaseError:
        return {
            "percent": get_deposit_percent(),
            "minimum_amount": Decimal("0.00"),
            "rounding": SalonSettings.DepositRounding.NONE,
        }


def calculate_deposit_amount(total):
    from .models import SalonSettings

    total = max(Decimal(total or 0), Decimal("0.00")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    deposit_settings = get_deposit_settings()
    amount = total * deposit_settings["percent"] / Decimal("100")
    if deposit_settings["rounding"] == SalonSettings.DepositRounding.UP_TO_EURO:
        amount = amount.quantize(Decimal("1"), rounding=ROUND_CEILING)
    else:
        amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    amount = max(amount, deposit_settings["minimum_amount"], Decimal("0.00"))
    return min(amount, total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
