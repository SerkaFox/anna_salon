from datetime import time
from decimal import Decimal

from django.test import Client as DjangoClient, TestCase
from django.urls import reverse
from django.utils.dateparse import parse_datetime

from accounts.models import User
from bookings.models import Booking
from clients.models import Client
from employees.models import Employee, EmployeeWeeklyShift
from services_app.models import Service


class PublicLegalPageTests(TestCase):
    def test_privacy_policy_page_returns_200(self):
        response = self.client.get(reverse("privacy_policy"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Política de privacidad")
        self.assertContains(response, "Instagram")
        self.assertContains(response, "serkafox@gmail.com")

    def test_terms_page_returns_200(self):
        response = self.client.get(reverse("terms"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Términos de servicio")
        self.assertContains(response, "BRIMOON Studio")

    def test_data_deletion_page_returns_200(self):
        response = self.client.get(reverse("data_deletion"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Instrucciones de eliminación de datos")
        self.assertContains(response, "desconectar")


class PublicBookingTests(TestCase):
    def setUp(self):
        self.browser = DjangoClient()
        self.service = Service.objects.create(
            name="Manicura",
            duration_minutes=60,
            price=Decimal("35.00"),
            requires_zone=False,
            is_active=True,
        )
        self.employee = Employee.objects.create(
            first_name="Lucia",
            last_name="Lopez",
            commission_percent=Decimal("40.00"),
            is_active=True,
        )
        self.employee.services.add(self.service)
        EmployeeWeeklyShift.objects.create(
            employee=self.employee,
            weekday=0,
            is_day_off=False,
            start_time=time(9, 0),
            end_time=time(18, 0),
        )
        self.date = "2026-05-25"

    def test_public_booking_page_loads(self):
        response = self.browser.get(reverse("public_booking"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reserva")
        self.assertContains(response, self.service.name)

    def test_public_booking_slots_returns_employee_options(self):
        response = self.browser.get(
            reverse("public_booking_slots"),
            {"service": self.service.pk, "date": self.date},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertGreater(len(payload["slots"]), 0)
        self.assertEqual(payload["slots"][0]["employees"][0]["id"], self.employee.pk)

    def test_public_booking_creates_user_client_and_pending_booking(self):
        slot_response = self.browser.get(
            reverse("public_booking_slots"),
            {"service": self.service.pk, "date": self.date},
        )
        slot = slot_response.json()["slots"][0]
        employee = slot["employees"][0]

        response = self.browser.post(
            reverse("public_booking"),
            {
                "service": self.service.pk,
                "employee": employee["id"],
                "zone": employee["zone"] or "",
                "start_at": slot["start_at"],
                "name": "Nueva Clienta",
                "password": "secret123",
            },
        )

        self.assertRedirects(response, reverse("clients:portal"))
        booking = Booking.objects.get(client__first_name="Nueva")
        self.assertEqual(booking.status, Booking.Statuses.PENDING)
        self.assertEqual(booking.source, Booking.Sources.WEBSITE)
        self.assertEqual(booking.service, self.service)
        user = User.objects.get(client_profile__first_name="Nueva")
        self.assertEqual(user.role, User.ROLE_CLIENT)
        self.assertTrue(user.check_password("secret123"))
        self.assertTrue(Client.objects.filter(user=user).exists())

    def test_public_booking_rejects_taken_slot(self):
        slot_response = self.browser.get(
            reverse("public_booking_slots"),
            {"service": self.service.pk, "date": self.date},
        )
        slot = slot_response.json()["slots"][0]
        employee = slot["employees"][0]
        existing_client = Client.objects.create(first_name="Maria")
        Booking.objects.create(
            client=existing_client,
            employee=self.employee,
            service=self.service,
            start_at=parse_datetime(slot["start_at"]),
            end_at=parse_datetime(slot["end_at"]),
            status=Booking.Statuses.CONFIRMED,
            source=Booking.Sources.MANUAL,
        )

        response = self.browser.post(
            reverse("public_booking"),
            {
                "service": self.service.pk,
                "employee": employee["id"],
                "start_at": slot["start_at"],
                "name": "Otra Clienta",
                "password": "secret123",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Este horario ya no esta disponible", status_code=400)
        self.assertFalse(User.objects.filter(first_name="Otra").exists())
