from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from bookings.models import Booking
from clients.models import Client
from employees.models import Employee
from services_app.models import Service

from .models import PushDevice
from .push_notifications import _notification_text, send_new_booking_notification


class PushDeviceApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="worker", password="secret", role=User.ROLE_EMPLOYEE
        )
        Employee.objects.create(user=self.user, first_name="Elena")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_employee_can_register_and_disable_device(self):
        url = reverse("mobile_api:push_devices")
        response = self.client.post(
            url,
            {"registration_token": "device-token", "platform": "android", "locale": "ru"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        device = PushDevice.objects.get()
        self.assertEqual(device.user, self.user)
        self.assertEqual(device.locale, "ru")

        response = self.client.delete(
            url, {"registration_token": "device-token"}, format="json"
        )
        self.assertEqual(response.status_code, 204)
        device.refresh_from_db()
        self.assertFalse(device.is_active)

    def test_client_account_cannot_register_worker_notifications(self):
        client_user = User.objects.create_user(
            username="client", password="secret", role=User.ROLE_CLIENT
        )
        self.client.force_authenticate(client_user)
        response = self.client.post(
            reverse("mobile_api:push_devices"),
            {"registration_token": "client-token"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_owner_can_register_management_notifications(self):
        owner = User.objects.create_user(
            username="owner", password="secret", role=User.ROLE_OWNER
        )
        self.client.force_authenticate(owner)
        response = self.client.post(
            reverse("mobile_api:push_devices"),
            {"registration_token": "owner-token", "locale": "ru"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(PushDevice.objects.filter(user=owner, is_active=True).exists())


@override_settings(
    FIREBASE_CREDENTIALS_FILE="/tmp/firebase.json",
    FIREBASE_PROJECT_ID="brimoon-test",
)
class BookingPushTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="worker", password="secret", role=User.ROLE_EMPLOYEE
        )
        self.employee = Employee.objects.create(user=self.user, first_name="Elena")
        self.client = Client.objects.create(first_name="Maria", last_name="Lopez")
        self.service = Service.objects.create(
            name="Manicura", duration_minutes=60, price=Decimal("40.00")
        )
        self.booking = Booking.objects.create(
            client=self.client,
            employee=self.employee,
            service=self.service,
            start_at=timezone.now() + timedelta(days=2),
            end_at=timezone.now() + timedelta(days=2, hours=1),
            duration_snapshot=60,
            price_snapshot=Decimal("40.00"),
            service_items_snapshot=[
                {"name": "Manicura"},
                {"name": "Pedicura"},
            ],
        )
        self.device = PushDevice.objects.create(
            user=self.user,
            registration_token="employee-device-token",
            locale=PushDevice.Locales.RUSSIAN,
        )

    def test_russian_message_contains_client_time_and_all_services(self):
        title, body = _notification_text(self.booking, "ru")
        self.assertEqual(title, "Новая запись")
        self.assertIn("Maria Lopez", body)
        self.assertIn("Manicura + Pedicura", body)

    @patch("mobile_api.push_notifications._firebase_app", return_value=object())
    @patch("firebase_admin.messaging.send")
    def test_notification_is_sent_only_to_assigned_employee(self, send, firebase_app):
        other_user = User.objects.create_user(
            username="other", password="secret", role=User.ROLE_EMPLOYEE
        )
        Employee.objects.create(user=other_user, first_name="Anna")
        PushDevice.objects.create(
            user=other_user,
            registration_token="other-device-token",
        )

        self.assertEqual(send_new_booking_notification(self.booking.pk), 1)
        message = send.call_args.args[0]
        self.assertEqual(message.token, "employee-device-token")
        self.assertEqual(message.data["booking_id"], str(self.booking.pk))

    @patch("mobile_api.push_notifications._firebase_app", return_value=object())
    @patch("firebase_admin.messaging.send")
    def test_notification_is_also_sent_to_owner(self, send, firebase_app):
        owner = User.objects.create_user(
            username="owner", password="secret", role=User.ROLE_OWNER
        )
        PushDevice.objects.create(
            user=owner,
            registration_token="owner-device-token",
        )

        self.assertEqual(send_new_booking_notification(self.booking.pk), 2)
        self.assertEqual(
            {call.args[0].token for call in send.call_args_list},
            {"employee-device-token", "owner-device-token"},
        )

    @patch("mobile_api.push_notifications.send_new_booking_notification")
    def test_creating_booking_schedules_one_push_after_commit(self, send):
        with self.captureOnCommitCallbacks(execute=True):
            booking = Booking.objects.create(
                client=self.client,
                employee=self.employee,
                service=self.service,
                start_at=timezone.now() + timedelta(days=3),
                end_at=timezone.now() + timedelta(days=3, hours=1),
                duration_snapshot=60,
                price_snapshot=Decimal("40.00"),
            )
        send.assert_called_once_with(booking.pk)
