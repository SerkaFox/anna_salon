from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from bookings.client_actions import cancel_booking, change_booking_service, reschedule_booking
from bookings.forms import BookingForm
from bookings.models import Booking
from clients.models import Client
from employees.models import Employee, EmployeeScheduleOverride, EmployeeWeeklyShift
from payments.models import Payment, PaymentRefund
from salon.models import Zone
from services_app.models import Service


class BookingScheduleTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(first_name="Ana")
        self.zone = Zone.objects.create(name="Cabina 1")
        self.service = Service.objects.create(
            name="Color",
            duration_minutes=60,
            price=Decimal("50.00"),
            requires_zone=True,
            is_active=True,
        )
        self.service.allowed_zones.add(self.zone)

        self.employee = Employee.objects.create(
            first_name="Lucia",
            last_name="Lopez",
            commission_percent=Decimal("40.00"),
            is_active=True,
        )
        self.employee.services.add(self.service)

        for weekday in range(7):
            EmployeeWeeklyShift.objects.create(
                employee=self.employee,
                weekday=weekday,
                is_day_off=weekday == 6,
                start_time=None if weekday == 6 else time(hour=9),
                end_time=None if weekday == 6 else time(hour=18),
                break_start=None if weekday == 6 else time(hour=13),
                break_end=None if weekday == 6 else time(hour=14),
            )

    def test_booking_rejected_during_break(self):
        start_at = timezone.make_aware(datetime(2026, 4, 20, 13, 0))
        end_at = timezone.make_aware(datetime(2026, 4, 20, 14, 0))

        form = BookingForm(data={
            "client": self.client_obj.pk,
            "employee": self.employee.pk,
            "service": self.service.pk,
            "zone": self.zone.pk,
            "start_at": start_at.strftime("%Y-%m-%dT%H:%M"),
            "end_at": end_at.strftime("%Y-%m-%dT%H:%M"),
            "status": "confirmed",
            "notes": "",
        })

        self.assertFalse(form.is_valid())
        self.assertIn("pausa", str(form.non_field_errors()).lower())

    def test_override_day_off_blocks_booking(self):
        EmployeeScheduleOverride.objects.create(
            employee=self.employee,
            date=date(2026, 4, 21),
            is_day_off=True,
            label="Vacaciones",
        )

        start_at = timezone.make_aware(datetime(2026, 4, 21, 10, 0))
        end_at = timezone.make_aware(datetime(2026, 4, 21, 11, 0))

        form = BookingForm(data={
            "client": self.client_obj.pk,
            "employee": self.employee.pk,
            "service": self.service.pk,
            "zone": self.zone.pk,
            "start_at": start_at.strftime("%Y-%m-%dT%H:%M"),
            "end_at": end_at.strftime("%Y-%m-%dT%H:%M"),
            "status": "confirmed",
            "notes": "",
        })

        self.assertFalse(form.is_valid())
        self.assertIn("no trabaja", str(form.non_field_errors()).lower())


@override_settings(BOOKING_FREE_CANCEL_HOURS=24, STRIPE_SECRET_KEY="sk_test_mock", STRIPE_CURRENCY="eur")
class ClientBookingActionTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(first_name="Ana", email="ana@example.test")
        self.zone = Zone.objects.create(name="Cabina 1", is_active=True)
        self.service = Service.objects.create(name="Color", duration_minutes=60, price=Decimal("50.00"), requires_zone=True, is_active=True)
        self.service.allowed_zones.add(self.zone)
        self.expensive_service = Service.objects.create(name="Color premium", duration_minutes=90, price=Decimal("80.00"), requires_zone=True, is_active=True)
        self.expensive_service.allowed_zones.add(self.zone)
        self.employee = Employee.objects.create(first_name="Lucia", last_name="Lopez", commission_percent=Decimal("40.00"), is_active=True)
        self.employee.services.add(self.service, self.expensive_service)
        for weekday in range(7):
            EmployeeWeeklyShift.objects.create(
                employee=self.employee,
                weekday=weekday,
                is_day_off=False,
                start_time=time(9, 0),
                end_time=time(18, 0),
                break_start=None,
                break_end=None,
            )

    def _booking(self, start_at=None, service=None):
        service = service or self.service
        start_at = start_at or timezone.now() + timedelta(days=3)
        start_at = start_at.replace(hour=10, minute=0, second=0, microsecond=0)
        return Booking.objects.create(
            client=self.client_obj,
            employee=self.employee,
            service=service,
            zone=self.zone,
            start_at=start_at,
            end_at=start_at + timedelta(minutes=service.duration_minutes),
            status=Booking.Statuses.CONFIRMED,
            source=Booking.Sources.WEBSITE,
            price_snapshot=service.price,
            duration_snapshot=service.duration_minutes,
            original_client_price_snapshot=service.price,
            client_price_snapshot=service.price,
            employee_percent_snapshot=Decimal("40.00"),
            employee_amount_snapshot=service.price * Decimal("0.40"),
            salon_amount_snapshot=service.price * Decimal("0.60"),
        )

    def _paid_payment(self, booking, amount=Decimal("10.00")):
        return Payment.objects.create(
            booking=booking,
            amount=amount,
            currency="eur",
            order_number=f"stripe-test-{booking.pk}-{amount}",
            provider=Payment.Providers.STRIPE,
            method=Payment.Methods.CARD,
            status=Payment.Statuses.PAID,
            stripe_payment_intent_id=f"pi_{booking.pk}",
            paid_at=timezone.now(),
        )

    @patch("payments.stripe_service.stripe.Refund.create")
    def test_cancellation_more_than_24h_triggers_refund_for_paid_booking(self, mocked_refund):
        booking = self._booking()
        payment = self._paid_payment(booking)
        mocked_refund.return_value = {"id": "re_test", "status": "succeeded"}

        message, refunds = cancel_booking(booking)

        booking.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(booking.status, Booking.Statuses.CANCELLED)
        self.assertIn("devolución", message.lower())
        self.assertEqual(len(refunds), 1)
        self.assertEqual(payment.status, Payment.Statuses.REFUNDED)
        self.assertEqual(payment.amount_refunded, Decimal("10.00"))
        self.assertTrue(PaymentRefund.objects.filter(stripe_refund_id="re_test").exists())

    @patch("payments.stripe_service.stripe.Refund.create")
    def test_cancellation_less_than_24h_cancels_without_refund(self, mocked_refund):
        booking = self._booking(start_at=timezone.now() + timedelta(hours=12))
        payment = self._paid_payment(booking)

        message, refunds = cancel_booking(booking)

        booking.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(booking.status, Booking.Statuses.CANCELLED)
        self.assertIn("no es reembolsable", message)
        self.assertEqual(refunds, [])
        mocked_refund.assert_not_called()
        self.assertEqual(payment.status, Payment.Statuses.PAID)

    def test_unpaid_booking_cancellation_works(self):
        booking = self._booking()

        message, refunds = cancel_booking(booking)

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Statuses.CANCELLED)
        self.assertEqual(refunds, [])
        self.assertIn("cancelado", message)

    def test_paid_booking_reschedule_preserves_payment(self):
        booking = self._booking()
        payment = self._paid_payment(booking)
        new_start = booking.start_at + timedelta(days=1)

        reschedule_booking(booking, start_at=new_start, employee=self.employee, zone=self.zone)

        booking.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(booking.start_at, new_start)
        self.assertEqual(payment.status, Payment.Statuses.PAID)

    def test_reschedule_into_unavailable_slot_rejected(self):
        booking = self._booking()
        other = self._booking(start_at=booking.start_at + timedelta(days=1))

        with self.assertRaises(Exception):
            reschedule_booking(booking, start_at=other.start_at, employee=self.employee, zone=self.zone)

    @patch("payments.stripe_service.stripe.checkout.Session.create")
    def test_adding_higher_price_service_creates_extra_payment(self, mocked_session):
        booking = self._booking()
        self._paid_payment(booking, amount=Decimal("50.00"))
        mocked_session.return_value = type("Session", (), {"id": "cs_extra", "url": "https://checkout.test/extra", "payment_intent": "pi_extra"})()

        result = change_booking_service(booking, service=self.expensive_service, request=type("Req", (), {"build_absolute_uri": lambda self, url: f"https://testserver{url}"})())

        self.assertEqual(result["extra_due"], Decimal("30.00"))
        self.assertIsNotNone(result["payment"])
        self.assertEqual(result["payment"].status, Payment.Statuses.EXTRA_PAYMENT_PENDING)
        self.assertEqual(result["payment"].checkout_url, "https://checkout.test/extra")
