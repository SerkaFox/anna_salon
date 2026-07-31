from datetime import datetime, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from bookings.models import Booking
from clients.models import Client
from documents.models import FiscalDocument, Payment
from employees.models import Employee
from services_app.models import Service


class CashboxAdminWebTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="cashbox-owner",
            password="testpass123",
            role=User.ROLE_OWNER,
        )
        self.client.force_login(self.user)
        client = Client.objects.create(first_name="Maria")
        employee = Employee.objects.create(first_name="Anna")
        service = Service.objects.create(
            name="Corte",
            duration_minutes=60,
            price=Decimal("50.00"),
        )
        start = timezone.make_aware(datetime(2026, 7, 20, 10, 0))
        booking = Booking.objects.create(
            client=client,
            employee=employee,
            service=service,
            start_at=start,
            end_at=start + timedelta(hours=1),
            status=Booking.Statuses.DONE,
            client_price_snapshot=Decimal("50.00"),
        )
        document = FiscalDocument.objects.create(
            booking=booking,
            issue_date=start.date(),
        )
        Payment.objects.create(
            fiscal_document=document,
            booking=booking,
            paid_at=start,
            amount=Decimal("20.00"),
            method=Payment.Methods.CASH,
        )
        Payment.objects.create(
            fiscal_document=document,
            booking=booking,
            paid_at=start + timedelta(days=2),
            amount=Decimal("30.00"),
            method=Payment.Methods.CARD,
        )

    def test_cashbox_filters_date_range_and_cash_plus_card(self):
        response = self.client.get(
            reverse("documents:cashbox"),
            {
                "date_from": "2026-07-20",
                "date_to": "2026-07-22",
                "method": "cash_card",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_range"])
        self.assertEqual(response.context["payments_count"], 2)
        self.assertEqual(response.context["payments_total"], Decimal("50.00"))
        self.assertEqual(response.context["totals_by_method"]["cash"], Decimal("20.00"))
        self.assertEqual(response.context["totals_by_method"]["card"], Decimal("30.00"))
        self.assertNotContains(response, "Cerrar caja")
