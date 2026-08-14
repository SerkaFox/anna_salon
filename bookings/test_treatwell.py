import json
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from decimal import Decimal

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from bookings.management.commands.import_treatwell_bookings import (
    Command as ImportCommand,
    Resolver,
)
from bookings.management.commands.export_treatwell_bookings import Command
from bookings.models import Booking
from bookings.treatwell import normalize_appointment
from clients.models import Client
from employees.models import Employee
from payments.models import Payment
from services_app.models import Service


class TreatwellNormalizationTests(SimpleTestCase):
    def test_normalizes_booking_fields_for_anna(self):
        stub = {"id": 42}
        detail = {
            "appointment": {
                "id": 42,
                "customer_id": 11,
                "staff_member_id": 22,
                "state": "checked_out",
                "time": "2026-08-15T10:30:00+02:00",
                "source_marketplace": "treatwell",
                "paid_online": True,
                "paid_online_amount": 2,
                "data": {
                    "treatment_id": 33,
                    "custom_price": 12,
                    "staff_member": {"first_name": "Ana", "last_name": "Lopez"},
                    "staff_member_treatment": {
                        "name": "Manicura",
                        "duration": 45,
                        "price": 15,
                        "venue_treatment_id": 44,
                    },
                },
            },
            "customer": {
                "id": 11,
                "first_name": "Maria",
                "last_name": "Perez",
                "phone": "+34123456789",
                "email": "maria@example.com",
            },
        }

        result = normalize_appointment(stub, detail)

        self.assertEqual(result["external_id"], "42")
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["end_at"], "2026-08-15T11:15:00+02:00")
        self.assertEqual(result["price"], "12.00")
        self.assertEqual(result["paid_online_amount"], "2.00")
        self.assertEqual(result["client"]["treatwell_id"], "11")
        self.assertEqual(result["employee"]["full_name"], "Ana Lopez")
        self.assertEqual(result["service"]["name"], "Manicura")

    def test_checkpoint_skips_incomplete_last_line(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.jsonl"
            path.write_text(
                json.dumps({"appointment_id": "1", "detail": {"appointment": {"id": 1}}})
                + "\n{unfinished",
                encoding="utf-8",
            )

            result = Command._read_checkpoint(path)

        self.assertEqual(list(result), ["1"])

    def test_deleted_detail_keeps_calendar_snapshot(self):
        result = normalize_appointment(
            {
                "id": 9,
                "state": "deleted",
                "time": "2026-08-15T10:00:00+02:00",
                "data": {
                    "staff_member": {"first_name": "Ana", "last_name": "B"},
                    "staff_member_treatment": {"name": "Manicura", "duration": 30, "price": 10},
                },
            },
            {"appointment": {"id": 9, "state": "deleted", "data": {}}},
        )

        self.assertEqual(result["employee"]["full_name"], "Ana B")
        self.assertEqual(result["service"]["name"], "Manicura")
        self.assertEqual(result["status"], "cancelled")


class TreatwellImportTests(TestCase):
    def test_save_is_idempotent_and_creates_treatwell_payment(self):
        client = Client.objects.create(first_name="Maria", phone="123456789")
        service = Service.objects.create(name="Manicura", duration_minutes=45, price=12)
        employee = Employee.objects.create(first_name="Ana", commission_percent=40)
        start_at = timezone.now()
        end_at = start_at + timedelta(minutes=45)
        row = {
            "external_id": "tw-42",
            "status": "confirmed",
            "notes": "",
            "price": "12.00",
            "duration_minutes": 45,
            "paid_online": True,
            "paid_online_amount": "2.00",
            "paid_online_at": start_at.isoformat(),
            "updated_at": start_at.isoformat(),
            "checked_out_at": "",
        }

        first_created, first_payment = ImportCommand._save(
            row, client, employee, service, start_at, end_at
        )
        second_created, second_payment = ImportCommand._save(
            row, client, employee, service, start_at, end_at
        )

        self.assertTrue(first_created)
        self.assertTrue(first_payment)
        self.assertFalse(second_created)
        self.assertFalse(second_payment)
        self.assertEqual(Booking.objects.count(), 1)
        payment = Payment.objects.get()
        self.assertEqual(payment.provider, Payment.Providers.TREATWELL)
        self.assertEqual(payment.amount, Decimal("2.00"))

    def test_resolver_uses_staff_surname_and_service_alias(self):
        Employee.objects.create(first_name="Hanna", last_name="Briukhovets")
        expected_service = Service.objects.create(name="Relleno de gel")
        resolver = Resolver()

        employee, employee_match = resolver.employee(
            {"first_name": "ANNA", "last_name": "Briukhovets-Flippo"}
        )
        service, service_match = resolver.service({"name": "Relleno de Acrygel"})

        self.assertEqual(employee.full_name, "Hanna Briukhovets")
        self.assertEqual(employee_match, "last_name")
        self.assertEqual(service, expected_service)
        self.assertEqual(service_match, "alias")

    def test_resolver_accepts_only_clear_unique_fuzzy_client_name(self):
        expected = Client.objects.create(first_name="Alejandra", last_name="Gonzalez")
        Client.objects.create(first_name="Maria", last_name="Lopez")
        resolver = Resolver()

        client, match = resolver.client({"full_name": "Alejandra Gonzales"})

        self.assertEqual(client, expected)
        self.assertEqual(match, "unique_fuzzy_name")
