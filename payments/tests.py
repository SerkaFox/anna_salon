import base64
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import stripe
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from bookings.models import Booking
from clients.models import Client
from employees.models import Employee
from payments.models import Payment
from payments.redsys import build_form_fields, decode_merchant_parameters, encode_merchant_parameters, sign_merchant_parameters, verify_signature
from salon.models import Zone
from services_app.models import Service


TEST_REDSYS_SECRET_KEY = base64.b64encode(b"0123456789abcdef01234567").decode("ascii")


@override_settings(
    REDSYS_MERCHANT_CODE="999008881",
    REDSYS_TERMINAL="001",
    REDSYS_SECRET_KEY=TEST_REDSYS_SECRET_KEY,
    REDSYS_ENVIRONMENT="test",
    REDSYS_CURRENCY="978",
    REDSYS_TRANSACTION_TYPE="0",
    PUBLIC_BASE_URL="https://example.test",
    ALLOWED_HOSTS=["testserver", "example.test"],
)
class RedsysPaymentTests(TestCase):
    def setUp(self):
        self.api_client = APIClient()
        self.owner_user = User.objects.create_user(
            username="owner",
            password="testpass123",
            role=User.ROLE_OWNER,
        )
        self.client_obj = Client.objects.create(first_name="Maria", last_name="Lopez")
        self.zone = Zone.objects.create(name="Cabina 1", is_active=True)
        self.service = Service.objects.create(
            name="Color",
            duration_minutes=60,
            price=Decimal("50.00"),
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
        start_at = timezone.make_aware(datetime(2026, 4, 27, 10, 0))
        self.booking = Booking.objects.create(
            employee=self.employee,
            client=self.client_obj,
            service=self.service,
            zone=self.zone,
            start_at=start_at,
            end_at=start_at + timedelta(minutes=self.service.duration_minutes),
            status=Booking.Statuses.CONFIRMED,
            source=Booking.Sources.MANUAL,
            price_snapshot=self.service.price,
            duration_snapshot=self.service.duration_minutes,
            original_client_price_snapshot=self.service.price,
            client_price_snapshot=self.service.price,
            discount_amount_snapshot=Decimal("0.00"),
            employee_percent_snapshot=Decimal("40.00"),
            employee_amount_snapshot=Decimal("20.00"),
            salon_amount_snapshot=Decimal("30.00"),
        )

    def test_payment_model_creation(self):
        payment = Payment.objects.create(
            booking=self.booking,
            amount=Decimal("15.00"),
            order_number="0001ABCDEF01",
        )

        self.assertEqual(payment.currency, "978")
        self.assertEqual(payment.provider, Payment.Providers.REDSYS)
        self.assertEqual(payment.method, Payment.Methods.UNKNOWN)
        self.assertEqual(payment.status, Payment.Statuses.PENDING)

    def test_redsys_signature_build_and_verify(self):
        parameters = {
            "DS_MERCHANT_AMOUNT": "1500",
            "DS_MERCHANT_ORDER": "0001ABCDEF01",
            "DS_MERCHANT_MERCHANTCODE": "999008881",
            "DS_MERCHANT_CURRENCY": "978",
            "DS_MERCHANT_TRANSACTIONTYPE": "0",
            "DS_MERCHANT_TERMINAL": "001",
        }

        fields = build_form_fields(parameters)
        decoded = decode_merchant_parameters(fields["Ds_MerchantParameters"])
        verified = verify_signature(fields["Ds_MerchantParameters"], fields["Ds_Signature"])

        self.assertEqual(fields["Ds_SignatureVersion"], "HMAC_SHA512_V1")
        self.assertEqual(decoded["DS_MERCHANT_ORDER"], "0001ABCDEF01")
        self.assertEqual(verified["DS_MERCHANT_AMOUNT"], "1500")

    def test_start_payment_endpoint(self):
        self.api_client.force_authenticate(user=self.owner_user)

        response = self.api_client.post(
            reverse("mobile_api:booking_payment", args=[self.booking.pk]),
            {"amount": "20.00", "method": "card"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        payment = Payment.objects.get(pk=payload["payment_id"])
        self.assertEqual(payment.booking, self.booking)
        self.assertEqual(payment.amount, Decimal("20.00"))
        self.assertEqual(payload["payment_url"], "https://sis-t.redsys.es:25443/sis/realizarPago")
        self.assertIn("Ds_MerchantParameters", payload["form_fields"])
        self.assertIn("Ds_Signature", payload["form_fields"])

    def test_redsys_notification_marks_paid(self):
        payment = self._create_pending_payment(order_number="0001ABCDEF02")
        fields = self._notification_fields(payment, response_code="0000", authorisation_code="123456")

        response = self.client.post(reverse("payments:redsys_notification"), fields)

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Statuses.PAID)
        self.assertEqual(payment.redsys_response_code, "0000")
        self.assertEqual(payment.redsys_authorisation_code, "123456")
        self.assertIsNotNone(payment.paid_at)

    def test_redsys_notification_marks_failed(self):
        payment = self._create_pending_payment(order_number="0001ABCDEF03")
        fields = self._notification_fields(payment, response_code="0180")

        response = self.client.post(reverse("payments:redsys_notification"), fields)

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Statuses.FAILED)
        self.assertEqual(payment.redsys_response_code, "0180")
        self.assertIsNone(payment.paid_at)

    def test_redsys_notification_rejects_invalid_signature(self):
        payment = self._create_pending_payment(order_number="0001ABCDEF04")
        fields = self._notification_fields(payment, response_code="0000")
        fields["Ds_Signature"] = "invalid"

        response = self.client.post(reverse("payments:redsys_notification"), fields)

        self.assertEqual(response.status_code, 400)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Statuses.PENDING)

    def _create_pending_payment(self, order_number):
        return Payment.objects.create(
            booking=self.booking,
            amount=Decimal("20.00"),
            order_number=order_number,
            method=Payment.Methods.CARD,
            status=Payment.Statuses.PENDING,
        )

    def _notification_fields(self, payment, *, response_code, authorisation_code=""):
        parameters = {
            "Ds_Amount": "2000",
            "Ds_Currency": "978",
            "Ds_Order": payment.order_number,
            "Ds_MerchantCode": "999008881",
            "Ds_Terminal": "001",
            "Ds_Response": response_code,
            "Ds_AuthorisationCode": authorisation_code,
            "Ds_TransactionType": "0",
        }
        encoded = encode_merchant_parameters(parameters)
        return {
            "Ds_SignatureVersion": "HMAC_SHA512_V1",
            "Ds_MerchantParameters": encoded,
            "Ds_Signature": sign_merchant_parameters(encoded, payment.order_number),
        }


@override_settings(
    STRIPE_SECRET_KEY="sk_test_mock",
    STRIPE_WEBHOOK_SECRET="whsec_mock",
    STRIPE_CURRENCY="eur",
    BOOKING_DEPOSIT_AMOUNT_EUR="10.00",
    PUBLIC_BASE_URL="https://example.test",
    ALLOWED_HOSTS=["testserver", "example.test"],
)
class StripePaymentTests(TestCase):
    def setUp(self):
        self.owner_user = User.objects.create_user(username="stripe-owner", password="testpass123", role=User.ROLE_OWNER)
        self.client_obj = Client.objects.create(first_name="Maria", last_name="Lopez", email="maria@example.test")
        self.service = Service.objects.create(name="Manicura", duration_minutes=60, price=Decimal("50.00"), requires_zone=False, is_active=True)
        self.employee = Employee.objects.create(first_name="Lucia", last_name="Lopez", commission_percent=Decimal("40.00"), is_active=True)
        self.employee.services.add(self.service)
        start_at = timezone.make_aware(datetime(2026, 4, 27, 10, 0))
        self.booking = Booking.objects.create(
            employee=self.employee,
            client=self.client_obj,
            service=self.service,
            start_at=start_at,
            end_at=start_at + timedelta(minutes=self.service.duration_minutes),
            status=Booking.Statuses.PENDING,
            source=Booking.Sources.WEBSITE,
            price_snapshot=self.service.price,
            duration_snapshot=self.service.duration_minutes,
            original_client_price_snapshot=self.service.price,
            client_price_snapshot=self.service.price,
            discount_amount_snapshot=Decimal("0.00"),
            employee_percent_snapshot=Decimal("40.00"),
            employee_amount_snapshot=Decimal("20.00"),
            salon_amount_snapshot=Decimal("30.00"),
        )

    @patch("payments.stripe_service.stripe.checkout.Session.create")
    def test_panel_checkout_creates_payment_and_returns_link(self, mocked_create):
        mocked_create.return_value = SimpleNamespace(
            id="cs_test_123",
            url="https://checkout.stripe.test/session",
            payment_intent="pi_test_123",
        )
        self.client.force_login(self.owner_user)

        response = self.client.post(reverse("bookings:stripe_checkout", args=[self.booking.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "https://checkout.stripe.test/session")
        payment = Payment.objects.get(provider=Payment.Providers.STRIPE)
        self.assertEqual(payment.booking, self.booking)
        self.assertEqual(payment.amount, Decimal("10.00"))
        self.assertEqual(payment.currency, "eur")
        self.assertEqual(payment.stripe_checkout_session_id, "cs_test_123")

    def test_webhook_invalid_signature_rejected(self):
        response = self.client.post(
            reverse("payments:stripe_webhook"),
            data=b'{"type":"checkout.session.completed"}',
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="bad",
        )

        self.assertEqual(response.status_code, 400)

    @patch("payments.views.verify_webhook_signature")
    def test_checkout_session_completed_marks_payment_paid(self, mocked_verify):
        payment = Payment.objects.create(
            booking=self.booking,
            amount=Decimal("10.00"),
            currency="eur",
            order_number="stripe-completed",
            provider=Payment.Providers.STRIPE,
            method=Payment.Methods.CARD,
            status=Payment.Statuses.PENDING,
            stripe_checkout_session_id="cs_completed",
        )
        mocked_verify.return_value = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_completed",
                    "payment_intent": "pi_completed",
                    "metadata": {"payment_id": str(payment.pk), "booking_id": str(self.booking.pk)},
                    "customer_details": {"email": "maria@example.test"},
                }
            },
        }

        response = self.client.post(
            reverse("payments:stripe_webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="valid",
        )

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.booking.refresh_from_db()
        self.assertEqual(payment.status, Payment.Statuses.PAID)
        self.assertEqual(payment.stripe_payment_intent_id, "pi_completed")
        self.assertIsNotNone(payment.paid_at)
        self.assertIsInstance(payment.raw_event, dict)
        self.assertEqual(payment.raw_event["type"], "checkout.session.completed")
        self.assertEqual(payment.raw_event["data"]["id"], "cs_completed")
        self.assertEqual(self.booking.status, Booking.Statuses.CONFIRMED)

    @patch("payments.views.verify_webhook_signature")
    def test_checkout_session_completed_accepts_stripe_event_object(self, mocked_verify):
        payment = Payment.objects.create(
            booking=self.booking,
            amount=Decimal("10.00"),
            currency="eur",
            order_number="stripe-event-object",
            provider=Payment.Providers.STRIPE,
            method=Payment.Methods.CARD,
            status=Payment.Statuses.PENDING,
            stripe_checkout_session_id="cs_event_object",
        )
        mocked_verify.return_value = stripe.Event.construct_from(
            {
                "id": "evt_completed_object",
                "type": "checkout.session.completed",
                "livemode": False,
                "created": 1780000000,
                "data": {
                    "object": {
                        "id": "cs_event_object",
                        "object": "checkout.session",
                        "payment_intent": "pi_event_object",
                        "metadata": {"payment_id": str(payment.pk), "booking_id": str(self.booking.pk)},
                        "customer_details": {"email": "maria@example.test"},
                    }
                },
            },
            "sk_test_mock",
        )

        response = self.client.post(
            reverse("payments:stripe_webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="valid",
        )

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Statuses.PAID)
        self.assertEqual(payment.raw_event["id"], "evt_completed_object")
        self.assertEqual(payment.raw_event["type"], "checkout.session.completed")
        self.assertEqual(payment.raw_event["data"]["id"], "cs_event_object")
        self.assertNotIn("Event", str(payment.raw_event))

    @patch("payments.views.verify_webhook_signature")
    def test_payment_intent_succeeded_accepts_stripe_event_object(self, mocked_verify):
        payment = Payment.objects.create(
            booking=self.booking,
            amount=Decimal("10.00"),
            currency="eur",
            order_number="stripe-intent-object",
            provider=Payment.Providers.STRIPE,
            method=Payment.Methods.CARD,
            status=Payment.Statuses.PENDING,
            stripe_payment_intent_id="pi_event_succeeded",
        )
        mocked_verify.return_value = stripe.Event.construct_from(
            {
                "id": "evt_intent_succeeded",
                "type": "payment_intent.succeeded",
                "livemode": False,
                "created": 1780000001,
                "data": {"object": {"id": "pi_event_succeeded", "object": "payment_intent"}},
            },
            "sk_test_mock",
        )

        response = self.client.post(
            reverse("payments:stripe_webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="valid",
        )

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Statuses.PAID)
        self.assertEqual(payment.raw_event["type"], "payment_intent.succeeded")

    @patch("payments.views.verify_webhook_signature")
    def test_unknown_stripe_event_returns_ok(self, mocked_verify):
        mocked_verify.return_value = stripe.Event.construct_from(
            {
                "id": "evt_unknown",
                "type": "customer.created",
                "livemode": False,
                "created": 1780000002,
                "data": {"object": {"id": "cus_test", "object": "customer"}},
            },
            "sk_test_mock",
        )

        response = self.client.post(
            reverse("payments:stripe_webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="valid",
        )

        self.assertEqual(response.status_code, 200)

    @patch("payments.views.verify_webhook_signature")
    def test_checkout_session_expired_marks_payment_expired(self, mocked_verify):
        payment = Payment.objects.create(
            booking=self.booking,
            amount=Decimal("10.00"),
            currency="eur",
            order_number="stripe-expired",
            provider=Payment.Providers.STRIPE,
            method=Payment.Methods.CARD,
            status=Payment.Statuses.PENDING,
            stripe_checkout_session_id="cs_expired",
        )
        mocked_verify.return_value = {
            "type": "checkout.session.expired",
            "data": {"object": {"id": "cs_expired", "metadata": {"payment_id": str(payment.pk)}}},
        }

        response = self.client.post(
            reverse("payments:stripe_webhook"),
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="valid",
        )

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Statuses.EXPIRED)

    def test_paid_booking_delete_becomes_cancelled_with_manual_refund_message(self):
        Payment.objects.create(
            booking=self.booking,
            amount=Decimal("10.00"),
            currency="eur",
            order_number="stripe-paid-delete",
            provider=Payment.Providers.STRIPE,
            method=Payment.Methods.CARD,
            status=Payment.Statuses.PAID,
            paid_at=timezone.now(),
        )
        self.client.force_login(self.owner_user)

        response = self.client.post(reverse("bookings:delete", args=[self.booking.pk]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Statuses.CANCELLED)
        self.assertContains(response, "Reembolso pendiente de gestión manual")
