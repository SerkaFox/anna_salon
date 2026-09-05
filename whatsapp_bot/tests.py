import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
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
    queue_payment_receipt,
    send_cancellation_confirmation,
    send_password_reset_credentials,
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

    def test_password_reset_sends_password_in_its_own_message(self):
        sent = send_password_reset_credentials(
            self.client_obj,
            username="ana600",
            password="CopyMe123",
        )

        self.assertTrue(sent)
        messages = list(
            WhatsAppMessage.objects.filter(
                kind=WhatsAppMessage.Kinds.PASSWORD_RESET
            ).order_by("created_at", "id")
        )
        self.assertEqual(len(messages), 2)
        self.assertNotIn("CopyMe123", messages[0].body)
        self.assertIn("ana600", messages[0].body)
        self.assertEqual(messages[1].body, "CopyMe123")

    def test_queue_confirmation_is_idempotent(self):
        booking = self._booking(timezone.now() + timedelta(days=3))

        first, first_created = queue_booking_confirmation(booking)
        second, second_created = queue_booking_confirmation(booking)

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(WhatsAppMessage.objects.count(), 1)

    def test_queue_payment_receipt_is_idempotent_and_contains_amount(self):
        booking = self._booking(timezone.now() + timedelta(days=1))
        payment = Payment.objects.create(
            booking=booking,
            amount=Decimal("15.00"),
            order_number=f"test-{booking.pk}",
            provider=Payment.Providers.STRIPE,
            status=Payment.Statuses.PAID,
        )

        first, first_created = queue_payment_receipt(booking, payment)
        second, second_created = queue_payment_receipt(booking, payment)

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.kind, WhatsAppMessage.Kinds.PAYMENT_RECEIPT)
        self.assertIn("15.00", first.body)
        self.assertEqual(
            WhatsAppMessage.objects.filter(
                booking=booking, kind=WhatsAppMessage.Kinds.PAYMENT_RECEIPT
            ).count(),
            1,
        )

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

        send_whatsapp_message(message)
        self.assertEqual(WhatsAppMessage.objects.count(), 1)

    @override_settings(WHATSAPP_DRY_RUN=False)
    @patch("whatsapp_bot.services.bridge.send_poll_message")
    def test_24h_reminder_uses_native_poll(self, send_poll):
        send_poll.return_value = {"message_id": "poll-message-1"}
        booking = self._booking(timezone.now() + timedelta(hours=24, minutes=5))
        message, _created = queue_booking_message(
            booking, kind=WhatsAppMessage.Kinds.REMINDER_24H
        )

        send_whatsapp_message(message)

        message.refresh_from_db()
        self.assertEqual(message.status, WhatsAppMessage.Statuses.SENT)
        self.assertEqual(message.provider_message_id, "poll-message-1")
        buttons = send_poll.call_args.kwargs["buttons"]
        self.assertEqual(buttons[0]["id"], f"attend_{booking.pk}")
        self.assertEqual(buttons[1]["id"], f"decline_{booking.pk}")

    @override_settings(WHATSAPP_DRY_RUN=False)
    @patch("whatsapp_bot.services.bridge.send_poll_message")
    def test_cancellation_confirmation_poll_puts_keep_option_first(self, send_poll):
        send_poll.return_value = {"message_id": "confirmation-poll-1"}
        booking = self._booking(timezone.now() + timedelta(hours=12))

        result = send_cancellation_confirmation(booking)

        self.assertEqual(result["message_id"], "confirmation-poll-1")
        buttons = send_poll.call_args.kwargs["buttons"]
        self.assertEqual(buttons[0], {"id": f"attend_{booking.pk}", "body": "No, mantener cita"})
        self.assertEqual(buttons[1], {"id": f"decline_{booking.pk}", "body": "Sí, cancelar"})

    @patch("whatsapp_bot.services.send_cancellation_confirmation")
    @patch("bookings.client_actions.create_refund")
    def test_decline_button_requires_second_confirmation(
        self, create_refund, send_confirmation
    ):
        booking = self._booking(timezone.now() + timedelta(hours=12))
        Payment.objects.create(
            booking=booking,
            amount=Decimal("10.00"),
            order_number=f"button-refund-{booking.pk}",
            provider=Payment.Providers.STRIPE,
            method=Payment.Methods.CARD,
            status=Payment.Statuses.PAID,
            stripe_payment_intent_id="pi_button_refund",
        )
        url = reverse("whatsapp_bot:button_reply")
        phone = normalize_whatsapp_phone(self.client_obj.phone).lstrip("+")

        premature = self.client.post(
            url,
            data=json.dumps(
                {
                    "button_id": f"confirm_decline_{booking.pk}",
                    "from_phone": phone,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(premature.status_code, 409)

        first = self.client.post(
            url,
            data=json.dumps(
                {"button_id": f"decline_{booking.pk}", "from_phone": phone}
            ),
            content_type="application/json",
        )

        self.assertEqual(first.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Statuses.CONFIRMED)
        self.assertEqual(
            booking.client_response,
            Booking.ClientResponses.CANCELLATION_PENDING,
        )
        send_confirmation.assert_called_once()
        create_refund.assert_not_called()

        create_refund.return_value = object()
        second = self.client.post(
            url,
            data=json.dumps(
                {
                    "button_id": f"decline_{booking.pk}",
                    "from_phone": phone,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(second.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Statuses.CANCELLED)
        self.assertEqual(booking.client_response, Booking.ClientResponses.DECLINED)
        create_refund.assert_called_once()

    @patch("whatsapp_bot.services.send_booking_kept_confirmation")
    def test_keep_poll_option_preserves_booking(self, send_kept):
        booking = self._booking(timezone.now() + timedelta(hours=12))
        booking.client_response = Booking.ClientResponses.CANCELLATION_PENDING
        booking.save(update_fields=["client_response"])
        url = reverse("whatsapp_bot:button_reply")
        phone = normalize_whatsapp_phone(self.client_obj.phone).lstrip("+")

        response = self.client.post(
            url,
            data=json.dumps(
                {"button_id": f"attend_{booking.pk}", "from_phone": phone}
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Statuses.CONFIRMED)
        self.assertEqual(booking.client_response, Booking.ClientResponses.ATTENDING)
        send_kept.assert_called_once()

        repeated_decline = self.client.post(
            url,
            data=json.dumps(
                {"button_id": f"decline_{booking.pk}", "from_phone": phone}
            ),
            content_type="application/json",
        )
        self.assertEqual(repeated_decline.status_code, 200)
        self.assertEqual(repeated_decline.json()["reason"], "response already recorded")
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Statuses.CONFIRMED)

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

    def test_pending_cancellation_confirmation_is_not_auto_cancelled(self):
        booking = self._booking(timezone.now() + timedelta(hours=23))
        booking.client_response = Booking.ClientResponses.CANCELLATION_PENDING
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

    def test_expired_prepayment_does_not_cancel_exempt_client(self):
        booking = self._booking(timezone.now() + timedelta(days=2))
        booking.client.prepayment_exempt = True
        booking.client.save(update_fields=["prepayment_exempt"])
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
        Payment.objects.create(
            booking=booking,
            amount=Decimal("5.00"),
            order_number="exempt-deposit-1",
            provider=Payment.Providers.STRIPE,
            method=Payment.Methods.CARD,
            status=Payment.Statuses.PENDING,
        )

        result = process_expired_prepayment_requests()

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Statuses.CONFIRMED)
        self.assertEqual(booking.prepayment_policy, Booking.PrepaymentPolicies.EXEMPT)
        self.assertIsNone(booking.prepayment_deadline_at)
        self.assertEqual(result["cancelled"], [])

    def test_default_24h_template_contains_confirmation_question(self):
        booking = self._booking(timezone.now() + timedelta(hours=24, minutes=5))

        message, created = queue_booking_confirmation(booking)
        self.assertTrue(created)

        reminder_body = TEMPLATE_DEFAULTS[WhatsAppMessage.Kinds.REMINDER_24H]
        self.assertIn("{date}", reminder_body)
        self.assertIn("{time}", reminder_body)
        self.assertIn("¿Vas a poder venir?", reminder_body)

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
