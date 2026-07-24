from decimal import Decimal, InvalidOperation

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
