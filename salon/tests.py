from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from salon.models import SalonSettings
from salon.preferences import calculate_deposit_amount


class DepositSettingsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="deposit-owner",
            password="testpass123",
            role=User.ROLE_OWNER,
        )
        self.settings = SalonSettings.load()
        self.settings.deposit_percent = Decimal("10.00")
        self.settings.deposit_minimum_amount = Decimal("2.00")
        self.settings.deposit_rounding = SalonSettings.DepositRounding.UP_TO_EURO
        self.settings.save()

    def test_global_deposit_rounds_up_and_applies_minimum(self):
        self.assertEqual(calculate_deposit_amount(Decimal("12.00")), Decimal("2.00"))
        self.assertEqual(calculate_deposit_amount(Decimal("21.00")), Decimal("3.00"))
        self.assertEqual(calculate_deposit_amount(Decimal("50.00")), Decimal("5.00"))
        self.assertEqual(calculate_deposit_amount(Decimal("1.50")), Decimal("1.50"))

    def test_rounding_can_be_disabled(self):
        self.settings.deposit_rounding = SalonSettings.DepositRounding.NONE
        self.settings.deposit_minimum_amount = Decimal("0.00")
        self.settings.save()
        self.assertEqual(calculate_deposit_amount(Decimal("12.00")), Decimal("1.20"))

    def test_owner_can_update_deposit_settings_on_site(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("salon:deposit_settings"),
            {
                "deposit_percent": "12.5",
                "deposit_minimum_amount": "3",
                "deposit_rounding": SalonSettings.DepositRounding.NONE,
            },
        )
        self.assertRedirects(response, reverse("salon:deposit_settings"))
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.deposit_percent, Decimal("12.50"))
        self.assertEqual(self.settings.deposit_minimum_amount, Decimal("3.00"))
        self.assertEqual(self.settings.deposit_rounding, SalonSettings.DepositRounding.NONE)
