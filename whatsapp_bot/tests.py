from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from bookings.models import Booking
from clients.models import Client
from employees.models import Employee
from services_app.models import Service

from .models import TEMPLATE_DEFAULTS, WhatsAppMessage, WhatsAppTemplate
from .services import (
    normalize_whatsapp_phone,
    queue_booking_confirmation,
    queue_due_reminders,
    send_due_messages,
    send_whatsapp_message,
)


@override_settings(WHATSAPP_DRY_RUN=True)
class WhatsAppBotTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(first_name="Ana", phone="600111222")
        self.service = Service.objects.create(
            name="Manicura",
            duration_minutes=60,
            price=Decimal("30.00"),
            is_active=True,
        )
        self.employee = Employee.objects.create(first_name="Lucia", is_active=True)
        self.employee.services.add(self.service)

    def _booking(self, start_at):
        return Booking.objects.create(
            client=self.client_obj,
            employee=self.employee,
            service=self.service,
            start_at=start_at,
            end_at=start_at + timedelta(minutes=60),
            status=Booking.Statuses.CONFIRMED,
            source=Booking.Sources.WEBSITE,
            price_snapshot=self.service.price,
            duration_snapshot=self.service.duration_minutes,
            original_client_price_snapshot=self.service.price,
            client_price_snapshot=self.service.price,
            employee_percent_snapshot=Decimal("40.00"),
            employee_amount_snapshot=Decimal("12.00"),
            salon_amount_snapshot=Decimal("18.00"),
        )

    def test_normalize_spanish_mobile_phone(self):
        self.assertEqual(normalize_whatsapp_phone("600 111 222"), "+34600111222")

    def test_queue_confirmation_is_idempotent(self):
        booking = self._booking(timezone.now() + timedelta(days=3))

        first, first_created = queue_booking_confirmation(booking)
        second, second_created = queue_booking_confirmation(booking)

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(WhatsAppMessage.objects.count(), 1)

    def test_queue_and_send_24h_reminder(self):
        self._booking(timezone.now() + timedelta(hours=24, minutes=5))

        result = queue_due_reminders(hours=24, window_minutes=15)
        processed = send_due_messages()

        self.assertEqual(len(result["queued"]), 1)
        self.assertEqual(len(processed), 1)
        message = WhatsAppMessage.objects.get(kind=WhatsAppMessage.Kinds.REMINDER_24H)
        self.assertEqual(message.status, WhatsAppMessage.Statuses.SENT)
        self.assertEqual(message.provider_message_id, "dry-run")
        self.assertIn("Voy:", message.body)
        self.assertIn("No voy:", message.body)
        self.assertEqual(message.body.count("/confirmar-cita/"), 2)

        send_whatsapp_message(message)
        self.assertEqual(WhatsAppMessage.objects.count(), 1)

    def test_default_24h_template_contains_confirmation_actions_once(self):
        booking = self._booking(timezone.now() + timedelta(hours=24, minutes=5))

        message, created = queue_booking_confirmation(booking)
        self.assertTrue(created)

        reminder_body = TEMPLATE_DEFAULTS[WhatsAppMessage.Kinds.REMINDER_24H]
        self.assertIn("{attend_url}", reminder_body)
        self.assertIn("{decline_url}", reminder_body)

    def test_done_booking_queues_delayed_review_request(self):
        template = WhatsAppTemplate.objects.get(
            kind=WhatsAppMessage.Kinds.REVIEW_REQUEST
        )
        template.body = "Privada: {review_url}\nGoogle: {google_review_url}"
        template.delay_minutes = 30
        template.save(update_fields=["body", "delay_minutes", "updated_at"])
        booking = self._booking(timezone.now() - timedelta(hours=2))

        booking.status = Booking.Statuses.DONE
        booking.save(update_fields=["status"])
        booking.refresh_from_db()

        message = WhatsAppMessage.objects.get(
            booking=booking,
            kind=WhatsAppMessage.Kinds.REVIEW_REQUEST,
        )
        self.assertIsNotNone(booking.completed_at)
        self.assertEqual(
            message.scheduled_for,
            max(booking.completed_at, booking.end_at)
            + timedelta(minutes=template.delay_minutes),
        )
        self.assertIn("/review/", message.body)
        self.assertIn("search.google.com", message.body)

        booking.save(update_fields=["notes"])
        self.assertEqual(
            WhatsAppMessage.objects.filter(
                booking=booking,
                kind=WhatsAppMessage.Kinds.REVIEW_REQUEST,
            ).count(),
            1,
        )
