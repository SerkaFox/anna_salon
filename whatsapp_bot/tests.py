from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from bookings.models import Booking
from clients.models import Client
from employees.models import Employee
from services_app.models import Service
from payments.models import Payment

from .models import (
    TEMPLATE_DEFAULTS,
    WhatsAppConnection,
    WhatsAppMessage,
    WhatsAppTemplate,
)
from .monitoring import refresh_connection_status
from .services import (
    normalize_whatsapp_phone,
    process_unanswered_24h_reminders,
    process_expired_prepayment_requests,
    queue_booking_message,
    queue_booking_confirmation,
    queue_due_reminders,
    send_due_messages,
    send_whatsapp_message,
)


class WhatsAppConnectionMonitoringTests(TestCase):
    @patch("whatsapp_bot.monitoring.bridge.get_status")
    def test_qr_status_replaces_stale_connected_state(self, get_status):
        connection = WhatsAppConnection.objects.create(
            name="main", status=WhatsAppConnection.Statuses.CONNECTED
        )
        get_status.return_value = {"status": "qr"}

        result = refresh_connection_status()

        connection.refresh_from_db()
        self.assertFalse(result["connected"])
        self.assertTrue(result["needs_reconnect"])
        self.assertEqual(connection.status, WhatsAppConnection.Statuses.QR_PENDING)

    @patch("whatsapp_bot.monitoring.bridge.get_status")
    def test_ready_status_is_connected(self, get_status):
        get_status.return_value = {"status": "ready", "phone": "+34600000000"}

        result = refresh_connection_status()

        self.assertTrue(result["connected"])
        self.assertFalse(result["needs_reconnect"])
        self.assertEqual(result["phone"], "+34600000000")


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

    @patch("bookings.client_actions.create_refund")
    def test_unanswered_24h_reminder_cancels_and_requests_refund(self, create_refund):
        booking = self._booking(timezone.now() + timedelta(hours=23))
        Payment.objects.create(
            booking=booking,
            amount=Decimal("10.00"),
            order_number="timeout-refund-1",
            provider=Payment.Providers.STRIPE,
            method=Payment.Methods.CARD,
            status=Payment.Statuses.PAID,
            stripe_payment_intent_id="pi_timeout_test",
        )
        message, _created = queue_booking_message(
            booking, kind=WhatsAppMessage.Kinds.REMINDER_24H
        )
        send_whatsapp_message(message)
        WhatsAppMessage.objects.filter(pk=message.pk).update(
            sent_at=timezone.now() - timedelta(minutes=16)
        )
        create_refund.return_value = object()

        result = process_unanswered_24h_reminders(timeout_minutes=15)
        send_due_messages()

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Statuses.CANCELLED)
        self.assertEqual(len(result["cancelled"]), 1)
        create_refund.assert_called_once()
        timeout_message = WhatsAppMessage.objects.get(
            booking=booking,
            kind=WhatsAppMessage.Kinds.REMINDER_TIMEOUT_CANCELLED,
        )
        self.assertEqual(timeout_message.status, WhatsAppMessage.Statuses.SENT)
        self.assertIn("devolución", timeout_message.body)

        second = process_unanswered_24h_reminders(timeout_minutes=15)
        self.assertEqual(len(second["cancelled"]), 0)
        create_refund.assert_called_once()

    def test_answered_24h_reminder_is_not_cancelled(self):
        booking = self._booking(timezone.now() + timedelta(hours=23))
        booking.client_response = Booking.ClientResponses.ATTENDING
        booking.client_responded_at = timezone.now()
        booking.save(update_fields=["client_response", "client_responded_at"])
        message, _created = queue_booking_message(
            booking, kind=WhatsAppMessage.Kinds.REMINDER_24H
        )
        send_whatsapp_message(message)
        WhatsAppMessage.objects.filter(pk=message.pk).update(
            sent_at=timezone.now() - timedelta(minutes=16)
        )

        result = process_unanswered_24h_reminders(timeout_minutes=15)

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Statuses.CONFIRMED)
        self.assertEqual(len(result["cancelled"]), 0)

    def test_expired_unpaid_prepayment_cancels_booking(self):
        booking = self._booking(timezone.now() + timedelta(days=2))
        booking.status = Booking.Statuses.PENDING
        booking.prepayment_policy = Booking.PrepaymentPolicies.REQUIRED
        booking.prepayment_requested_at = timezone.now() - timedelta(minutes=31)
        booking.prepayment_deadline_at = timezone.now() - timedelta(minutes=1)
        booking.save(
            update_fields=[
                "status",
                "prepayment_policy",
                "prepayment_requested_at",
                "prepayment_deadline_at",
            ]
        )
        payment = Payment.objects.create(
            booking=booking,
            amount=Decimal("5.00"),
            order_number="expired-deposit-1",
            provider=Payment.Providers.STRIPE,
            method=Payment.Methods.CARD,
            status=Payment.Statuses.PENDING,
        )

        result = process_expired_prepayment_requests()
        send_due_messages()

        booking.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(booking.status, Booking.Statuses.CANCELLED)
        self.assertEqual(payment.status, Payment.Statuses.EXPIRED)
        self.assertEqual(len(result["cancelled"]), 1)
        self.assertTrue(
            WhatsAppMessage.objects.filter(
                booking=booking,
                kind=WhatsAppMessage.Kinds.PREPAYMENT_TIMEOUT_CANCELLED,
                status=WhatsAppMessage.Statuses.SENT,
            ).exists()
        )

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
