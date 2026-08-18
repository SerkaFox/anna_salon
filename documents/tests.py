from datetime import datetime, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from bookings.models import Booking
from clients.models import Client
from documents.line_items import delete_document_line, update_document_line_price
from documents.models import FiscalDocument, FiscalDocumentLine, Payment
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


class FiscalDocumentLineEditingTests(TestCase):
    def setUp(self):
        client = Client.objects.create(first_name="Maria")
        employee = Employee.objects.create(first_name="Anna")
        service = Service.objects.create(
            name="Peinado",
            duration_minutes=60,
            price=Decimal("20.00"),
        )
        start = timezone.make_aware(datetime(2026, 8, 18, 10, 0))
        booking = Booking.objects.create(
            client=client,
            employee=employee,
            service=service,
            start_at=start,
            end_at=start + timedelta(hours=1),
            status=Booking.Statuses.DONE,
            client_price_snapshot=Decimal("20.00"),
        )
        self.document = FiscalDocument.objects.create(booking=booking)
        self.line = FiscalDocumentLine.objects.create(
            fiscal_document=self.document,
            service=service,
            description="Peinado",
            quantity=Decimal("1.00"),
            unit_amount=Decimal("20.00"),
        )
        self.document.save()

    def test_price_can_be_changed(self):
        update_document_line_price(self.line.pk, Decimal("23.00"))

        self.line.refresh_from_db()
        self.document.refresh_from_db()
        self.assertEqual(self.line.unit_amount, Decimal("23.00"))
        self.assertEqual(self.document.total_amount, Decimal("23.00"))

    def test_last_line_cannot_be_deleted(self):
        with self.assertRaises(ValidationError):
            delete_document_line(self.line.pk)

        self.assertTrue(FiscalDocumentLine.objects.filter(pk=self.line.pk).exists())

    def test_extra_line_can_be_deleted(self):
        extra = FiscalDocumentLine.objects.create(
            fiscal_document=self.document,
            description="Lavado",
            quantity=Decimal("1.00"),
            unit_amount=Decimal("3.00"),
        )
        self.document.save()

        delete_document_line(extra.pk)

        self.document.refresh_from_db()
        self.assertFalse(FiscalDocumentLine.objects.filter(pk=extra.pk).exists())
        self.assertEqual(self.document.total_amount, Decimal("20.00"))
