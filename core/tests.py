from datetime import datetime, timedelta, time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import Client as DjangoClient, TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts.models import User
from bookings.models import Booking, BookingWaitlistEntry
from clients.models import Client
from employees.models import Employee, EmployeeWeeklyShift
from services_app.models import Service


class PublicLegalPageTests(TestCase):
    def test_home_page_contains_real_contact_details(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rafaela Ybarra Kalea, 2 bis, Deusto, 48014 Bilbao, Bizkaia")
        self.assertContains(response, "643996431")
        self.assertContains(response, "https://maps.app.goo.gl/MuEAzwCAtxvbriCC9")
        self.assertContains(response, "google.com/maps/embed")

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


class ProgressiveWebAppTests(TestCase):
    def test_home_exposes_install_metadata(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("web_app_manifest"))
        self.assertContains(response, "data-pwa-install")

    def test_manifest_is_installable(self):
        response = self.client.get(reverse("web_app_manifest"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/manifest+json")
        self.assertEqual(response.json()["display"], "standalone")
        self.assertEqual(response.json()["start_url"], "/app-start/")

    def test_service_worker_has_root_scope(self):
        response = self.client.get(reverse("service_worker"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        self.assertContains(response, "self.addEventListener('fetch'")


class PublicBookingTests(TestCase):
    def setUp(self):
        self.browser = DjangoClient()
        self.service = Service.objects.create(
            name="Manicura",
            category=Service.Categories.MANICURE,
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
        next_day = timezone.localdate() + timedelta(days=2)
        while next_day.weekday() != 0:
            next_day += timedelta(days=1)
        self.date = next_day.isoformat()

    def test_public_booking_page_loads(self):
        response = self.browser.get(reverse("public_booking"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reserva")
        self.assertContains(response, self.service.name)
        self.assertContains(response, "Lista de servicios")
        self.assertContains(response, "35,00 €")
        self.assertContains(response, reverse("mobile_api:password_recovery"))
        self.assertContains(response, "¿Olvidaste la contraseña?")
        self.assertContains(response, "data-password-recovery-modal")
        self.assertContains(response, "password-visibility__toggle")

    def test_public_waitlist_accepts_date_range_and_notes(self):
        date_to = (datetime.strptime(self.date, "%Y-%m-%d").date() + timedelta(days=4)).isoformat()

        response = self.browser.post(
            reverse("public_waitlist"),
            {
                "service": self.service.pk,
                "employee": self.employee.pk,
                "date": self.date,
                "date_to": date_to,
                "name": "Maria",
                "phone": "+34600111222",
                "notes": "Llamar si alguien cancela",
            },
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200, response.json())
        entry = BookingWaitlistEntry.objects.get(pk=response.json()["waitlist_id"])
        self.assertEqual(entry.desired_date_to.isoformat(), date_to)
        self.assertEqual(entry.notes, "Llamar si alguien cancela")

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

    @patch("core.views.request_booking_prepayment")
    def test_public_booking_creates_pending_booking_with_required_prepayment(
        self, request_prepayment
    ):
        request_prepayment.return_value = SimpleNamespace(
            checkout_url="https://checkout.stripe.test/public"
        )
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
                "contact": "+34600111222",
            },
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            response.json()["checkout_url"],
            "https://checkout.stripe.test/public",
        )
        self.assertIn("whatsapp_action", response.json())
        booking = Booking.objects.get(client__first_name="Nueva")
        self.assertEqual(booking.client.phone, "+34600111222")
        self.assertEqual(booking.status, Booking.Statuses.PENDING)
        self.assertEqual(
            booking.prepayment_policy, Booking.PrepaymentPolicies.REQUIRED
        )
        self.assertEqual(booking.source, Booking.Sources.WEBSITE)
        self.assertEqual(booking.service, self.service)
        user = User.objects.get(client_profile__first_name="Nueva")
        self.assertEqual(user.phone, "+34600111222")
        self.assertEqual(user.role, User.ROLE_CLIENT)
        self.assertTrue(user.check_password("secret123"))
        self.assertTrue(Client.objects.filter(user=user).exists())
        request_prepayment.assert_called_once()

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
                "contact": "+34600111222",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Este horario ya no esta disponible", status_code=400)
        self.assertFalse(User.objects.filter(first_name="Otra").exists())

    def test_public_booking_requires_phone_or_email_for_new_account(self):
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
                "name": "Clienta Sin Contacto",
                "password": "secret123",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Indica telefono o email", status_code=400)
        self.assertFalse(User.objects.filter(first_name="Clienta").exists())

    def test_public_booking_rejects_phone_number_as_client_name(self):
        response = self.browser.post(
            reverse("public_booking"),
            {
                "cart_json": '[{"service": 1}]',
                "name": "+34 600 111 222",
                "password": "secret123",
                "contact": "+34600111222",
            },
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("nombre", response.json()["errors"]["name"][0].lower())
        self.assertFalse(Client.objects.filter(first_name__startswith="+34").exists())

    def test_public_booking_page_has_name_reminder_modal(self):
        response = self.browser.get(reverse("public_booking"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-name-reminder-modal")
        self.assertContains(response, "¿Cómo te llamas?")

    def test_public_booking_slots_allow_dates_up_to_one_year_ahead(self):
        future_date = timezone.localdate() + timedelta(days=300)
        while future_date.weekday() != 0:
            future_date += timedelta(days=1)

        response = self.browser.get(
            reverse("public_booking_slots"),
            {"service": self.service.pk, "date": future_date.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])


class ClientIdentityLoginTests(TestCase):
    def test_login_page_has_password_visibility_toggle(self):
        response = self.client.get(reverse("accounts:login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "password-visibility__toggle")

    def test_login_form_accepts_client_email(self):
        user = User.objects.create_user(
            username="client_email",
            password="secret123",
            role=User.ROLE_CLIENT,
            email="client@example.com",
        )

        response = self.client.post(reverse("accounts:login"), {"username": "client@example.com", "password": "secret123"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_login_form_accepts_client_phone(self):
        user = User.objects.create_user(
            username="client_phone",
            password="secret123",
            role=User.ROLE_CLIENT,
            phone="+34 600 111 222",
        )

        response = self.client.post(reverse("accounts:login"), {"username": "600111222", "password": "secret123"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
