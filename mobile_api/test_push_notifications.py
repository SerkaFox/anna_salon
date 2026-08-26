from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from bookings.models import Booking
from bookings.models import BookingPrepayment
from clients.models import Client
from employees.models import Employee
from services_app.models import Service

from .models import PushDevice
from .push_notifications import (
    EVENT_BOOKING_CANCELLED,
    EVENT_BOOKING_RESCHEDULED,
    EVENT_PREPAYMENT_RECEIVED,
    _notification_text,
    send_booking_notification,
    send_new_booking_notification,
)


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

    def test_employee_can_read_and_update_notification_preferences(self):
        PushDevice.objects.create(
            user=self.user,
            registration_token="preferences-token",
        )
        url = reverse("mobile_api:push_device_preferences")

        response = self.client.post(
            url,
            {"registration_token": "preferences-token"},
            format="json",
        )
        self.assertTrue(response.json()["preferences"]["booking_cancelled"])

        response = self.client.patch(
            url,
            {
                "registration_token": "preferences-token",
                "preferences": {
                    "booking_cancelled": False,
                    "reminder_2h": False,
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["preferences"]["booking_cancelled"])
        self.assertFalse(response.json()["preferences"]["reminder_2h"])

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

        title, body = _notification_text(
            self.booking,
            "ru",
            EVENT_BOOKING_CANCELLED,
        )
        self.assertEqual(title, "Запись отменена")
        self.assertIn("Maria Lopez", body)

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

    @patch("mobile_api.push_notifications._firebase_app", return_value=object())
    @patch("firebase_admin.messaging.send")
    def test_disabled_event_is_not_sent_to_device(self, send, firebase_app):
        self.device.notify_booking_cancelled = False
        self.device.save(update_fields=["notify_booking_cancelled"])

        self.assertEqual(
            send_booking_notification(self.booking.pk, EVENT_BOOKING_CANCELLED),
            0,
        )
        send.assert_not_called()

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

    @patch("mobile_api.push_notifications.send_booking_notification")
    def test_rescheduling_booking_schedules_push(self, send):
        new_start = self.booking.start_at + timedelta(hours=2)
        with self.captureOnCommitCallbacks(execute=True):
            self.booking.start_at = new_start
            self.booking.end_at = new_start + timedelta(hours=1)
            self.booking.save(update_fields=["start_at", "end_at", "updated_at"])

        send.assert_called_once()
        self.assertEqual(send.call_args.args[:2], (self.booking.pk, EVENT_BOOKING_RESCHEDULED))

    @patch("mobile_api.push_notifications.send_booking_notification")
    def test_paid_prepayment_schedules_push(self, send):
        from payments.models import Payment

        payment = Payment.objects.create(
            booking=self.booking,
            amount=Decimal("10.00"),
            order_number="push-payment",
            status=Payment.Statuses.PAID,
        )
        with self.captureOnCommitCallbacks(execute=True):
            BookingPrepayment.objects.create(
                booking=self.booking,
                payment=payment,
                amount=Decimal("10.00"),
                status=BookingPrepayment.Statuses.PAID,
                refundable_until=self.booking.start_at - timedelta(hours=24),
            )

        send.assert_called_once_with(
            self.booking.pk,
            EVENT_PREPAYMENT_RECEIVED,
            context={"amount": "10.00"},
        )
