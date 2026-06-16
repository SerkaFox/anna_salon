from datetime import datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from bookings.models import Booking
from clients.models import Client
from employees.models import Employee, EmployeeRecurringTimeBlock, EmployeeTimeBlock, EmployeeWeeklyShift
from payments.models import Payment
from salon.models import Zone
from services_app.models import Service


class MobileApiMvpTests(TestCase):
    def setUp(self):
        self.api_client = APIClient()

        self.owner_user = User.objects.create_user(
            username="owner",
            password="testpass123",
            role=User.ROLE_OWNER,
        )
        self.employee_user = User.objects.create_user(
            username="employee",
            password="testpass123",
            role=User.ROLE_EMPLOYEE,
        )
        self.other_employee_user = User.objects.create_user(
            username="other",
            password="testpass123",
            role=User.ROLE_EMPLOYEE,
        )

        self.client_obj = Client.objects.create(first_name="Maria", last_name="Lopez")
        self.other_client = Client.objects.create(first_name="Sofia", last_name="Diaz")

        self.zone = Zone.objects.create(name="Cabina 1", is_active=True)
        self.other_zone = Zone.objects.create(name="Cabina 2", is_active=True)

        self.service = Service.objects.create(
            name="Color",
            duration_minutes=60,
            price=Decimal("50.00"),
            requires_zone=True,
            is_active=True,
        )
        self.service.allowed_zones.add(self.zone, self.other_zone)

        self.no_zone_service = Service.objects.create(
            name="Corte",
            duration_minutes=45,
            price=Decimal("30.00"),
            requires_zone=False,
            is_active=True,
        )

        self.employee = Employee.objects.create(
            user=self.employee_user,
            first_name="Lucia",
            last_name="Lopez",
            commission_percent=Decimal("40.00"),
            is_active=True,
        )
        self.employee.services.add(self.service, self.no_zone_service)

        self.other_employee = Employee.objects.create(
            user=self.other_employee_user,
            first_name="Elena",
            last_name="Ruiz",
            commission_percent=Decimal("40.00"),
            is_active=True,
        )
        self.other_employee.services.add(self.service)

        self.unsupported_service = Service.objects.create(
            name="Maquillaje",
            duration_minutes=60,
            price=Decimal("80.00"),
            requires_zone=False,
            is_active=True,
        )

        self._ensure_weekly_shifts(self.employee)
        self._ensure_weekly_shifts(self.other_employee)
        self.base_start = timezone.make_aware(datetime(2026, 4, 27, 10, 0))

    def _ensure_weekly_shifts(self, employee):
        for weekday in range(7):
            EmployeeWeeklyShift.objects.create(
                employee=employee,
                weekday=weekday,
                is_day_off=weekday == 6,
                start_time=None if weekday == 6 else time(9, 0),
                end_time=None if weekday == 6 else time(18, 0),
                break_start=None,
                break_end=None,
            )

    def _create_booking(self, *, employee=None, client=None, service=None, zone=None, start_at=None, status=Booking.Statuses.CONFIRMED):
        employee = employee or self.employee
        client = client or self.client_obj
        service = service or self.service
        start_at = start_at or self.base_start
        return Booking.objects.create(
            employee=employee,
            client=client,
            service=service,
            zone=zone if zone is not None else self.zone,
            start_at=start_at,
            end_at=start_at + timedelta(minutes=service.duration_minutes),
            status=status,
            source=Booking.Sources.MANUAL,
            price_snapshot=service.price,
            duration_snapshot=service.duration_minutes,
            original_client_price_snapshot=service.price,
            client_price_snapshot=service.price,
            discount_amount_snapshot=Decimal("0.00"),
            employee_percent_snapshot=Decimal("40.00"),
            employee_amount_snapshot=Decimal("20.00"),
            salon_amount_snapshot=Decimal("30.00"),
        )

    def _auth(self, user):
        self.api_client.force_authenticate(user=user)

    def _booking_payload(self, **overrides):
        payload = {
            "client": self.client_obj.pk,
            "employee": self.employee.pk,
            "service": self.service.pk,
            "zone": self.zone.pk,
            "start_at": "2026-04-27T10:00:00+02:00",
            "source": Booking.Sources.MANUAL,
            "notes": "",
        }
        payload.update(overrides)
        return payload

    def test_unauthenticated_requests_return_401_or_403(self):
        for url in (
            reverse("mobile_api:me"),
            reverse("mobile_api:clients"),
            reverse("mobile_api:bookings"),
        ):
            response = self.api_client.get(url)
            self.assertIn(response.status_code, {401, 403})

    def test_authenticated_user_can_access_me(self):
        self._auth(self.owner_user)

        response = self.api_client.get(reverse("mobile_api:me"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["username"], "owner")

    def test_list_endpoints_return_json(self):
        self._auth(self.owner_user)

        for url in (
            reverse("mobile_api:clients"),
            reverse("mobile_api:services"),
            reverse("mobile_api:employees"),
            reverse("mobile_api:zones"),
        ):
            response = self.api_client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "application/json")
            self.assertIsInstance(response.json(), list)

    def test_owner_can_see_all_bookings(self):
        own_booking = self._create_booking(employee=self.employee, client=self.client_obj)
        other_booking = self._create_booking(
            employee=self.other_employee,
            client=self.other_client,
            zone=self.other_zone,
            start_at=self.base_start + timedelta(hours=2),
        )
        self._auth(self.owner_user)

        response = self.api_client.get(reverse("mobile_api:bookings"), {"date": "2026-04-27"})

        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.json()["results"]}
        self.assertEqual(ids, {own_booking.pk, other_booking.pk})

    def test_employee_can_see_team_bookings(self):
        own_booking = self._create_booking(employee=self.employee, client=self.client_obj)
        other_booking = self._create_booking(
            employee=self.other_employee,
            client=self.other_client,
            zone=self.other_zone,
            start_at=self.base_start + timedelta(hours=2),
        )
        self._auth(self.employee_user)

        response = self.api_client.get(reverse("mobile_api:bookings"), {"date": "2026-04-27"})

        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.json()["results"]}
        self.assertEqual(ids, {own_booking.pk, other_booking.pk})

    def test_booking_creation_rejects_overlapping_employee_booking(self):
        self._create_booking(employee=self.employee, zone=self.zone)
        self._auth(self.owner_user)

        response = self.api_client.post(
            reverse("mobile_api:bookings"),
            self._booking_payload(zone=self.other_zone.pk, start_at="2026-04-27T10:30:00+02:00"),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("empleado ya tiene una reserva", str(response.json()).lower())

    def test_booking_creation_rejects_overlapping_zone_booking(self):
        self._create_booking(employee=self.employee, zone=self.zone)
        self._auth(self.owner_user)

        response = self.api_client.post(
            reverse("mobile_api:bookings"),
            self._booking_payload(employee=self.other_employee.pk, zone=self.zone.pk, start_at="2026-04-27T10:30:00+02:00"),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("zona ya está ocupada", str(response.json()).lower())

    def test_booking_creation_rejects_service_not_allowed_for_employee(self):
        self._auth(self.owner_user)

        response = self.api_client.post(
            reverse("mobile_api:bookings"),
            self._booking_payload(service=self.unsupported_service.pk, zone=None),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("no realiza el servicio", str(response.json()).lower())

    def test_booking_creation_auto_assigns_zone_when_service_requires_zone(self):
        self._auth(self.owner_user)

        response = self.api_client.post(
            reverse("mobile_api:bookings"),
            self._booking_payload(zone=None),
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["zone"], self.zone.pk)

    def test_check_availability_returns_available_true_or_false_with_spanish_reason(self):
        self._auth(self.owner_user)

        available_response = self.api_client.post(
            reverse("mobile_api:booking_check_availability"),
            self._booking_payload(start_at="2026-04-27T12:00:00+02:00"),
            format="json",
        )
        self.assertEqual(available_response.status_code, 200)
        self.assertIs(available_response.json()["available"], True)
        self.assertEqual(available_response.json()["message"], "Horario disponible.")

        self._create_booking(employee=self.employee, zone=self.zone)
        unavailable_response = self.api_client.post(
            reverse("mobile_api:booking_check_availability"),
            self._booking_payload(start_at="2026-04-27T10:30:00+02:00"),
            format="json",
        )
        self.assertEqual(unavailable_response.status_code, 200)
        self.assertIs(unavailable_response.json()["available"], False)
        self.assertIn("horario no está disponible", unavailable_response.json()["message"].lower())

    def test_owner_can_create_list_update_and_delete_time_block(self):
        self._auth(self.owner_user)

        create_response = self.api_client.post(
            reverse("mobile_api:time_blocks"),
            {
                "employee": self.employee.pk,
                "start_at": "2026-04-27T14:00:00+02:00",
                "end_at": "2026-04-27T14:30:00+02:00",
                "reason": "Pausa",
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, 201)
        block_id = create_response.json()["id"]
        self.assertEqual(create_response.json()["reason"], "Pausa")

        list_response = self.api_client.get(
            reverse("mobile_api:time_blocks"),
            {"date": "2026-04-27", "employee": self.employee.pk},
        )
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual([item["id"] for item in list_response.json()["results"]], [block_id])

        patch_response = self.api_client.patch(
            reverse("mobile_api:time_block_detail", args=[block_id]),
            {
                "end_at": "2026-04-27T14:45:00+02:00",
                "reason": "Descanso",
            },
            format="json",
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.json()["reason"], "Descanso")
        self.assertEqual(patch_response.json()["end_time"], "14:45:00")

        delete_response = self.api_client.delete(reverse("mobile_api:time_block_detail", args=[block_id]))
        self.assertEqual(delete_response.status_code, 204)
        self.assertFalse(EmployeeTimeBlock.objects.filter(pk=block_id).exists())

    def test_employee_can_manage_only_own_time_blocks(self):
        other_block = EmployeeTimeBlock.objects.create(
            employee=self.other_employee,
            date=self.base_start.date(),
            start_time=time(14, 0),
            end_time=time(14, 30),
            label="Pausa",
        )
        self._auth(self.employee_user)

        forbidden_update = self.api_client.patch(
            reverse("mobile_api:time_block_detail", args=[other_block.pk]),
            {"reason": "No permitido"},
            format="json",
        )
        self.assertEqual(forbidden_update.status_code, 403)

        forbidden_create = self.api_client.post(
            reverse("mobile_api:time_blocks"),
            {
                "employee": self.other_employee.pk,
                "start_at": "2026-04-27T14:00:00+02:00",
                "end_at": "2026-04-27T14:30:00+02:00",
                "reason": "Pausa",
            },
            format="json",
        )
        self.assertEqual(forbidden_create.status_code, 400)
        self.assertIn("sin acceso", str(forbidden_create.json()).lower())

    def test_time_block_rejects_overlap_with_block_and_booking_unless_forced(self):
        EmployeeTimeBlock.objects.create(
            employee=self.employee,
            date=self.base_start.date(),
            start_time=time(14, 0),
            end_time=time(14, 30),
            label="Pausa",
        )
        self._auth(self.owner_user)

        block_overlap = self.api_client.post(
            reverse("mobile_api:time_blocks"),
            {
                "employee": self.employee.pk,
                "start_at": "2026-04-27T14:15:00+02:00",
                "end_at": "2026-04-27T14:45:00+02:00",
                "reason": "Pausa",
            },
            format="json",
        )
        self.assertEqual(block_overlap.status_code, 400)
        self.assertIn("se solapa con otro bloqueo", str(block_overlap.json()).lower())

        self._create_booking(employee=self.employee, zone=self.zone)
        booking_overlap = self.api_client.post(
            reverse("mobile_api:time_blocks"),
            {
                "employee": self.employee.pk,
                "start_at": "2026-04-27T10:15:00+02:00",
                "end_at": "2026-04-27T10:45:00+02:00",
                "reason": "Pausa",
            },
            format="json",
        )
        self.assertEqual(booking_overlap.status_code, 400)
        self.assertIn("reserva existente", str(booking_overlap.json()).lower())

        forced_response = self.api_client.post(
            reverse("mobile_api:time_blocks"),
            {
                "employee": self.employee.pk,
                "start_at": "2026-04-27T10:15:00+02:00",
                "end_at": "2026-04-27T10:45:00+02:00",
                "reason": "Pausa",
                "force": True,
            },
            format="json",
        )
        self.assertEqual(forced_response.status_code, 201)

    def test_calendar_day_includes_one_time_and_recurring_time_blocks(self):
        one_time = EmployeeTimeBlock.objects.create(
            employee=self.employee,
            date=self.base_start.date(),
            start_time=time(14, 0),
            end_time=time(14, 30),
            label="Pausa puntual",
        )
        recurring = EmployeeRecurringTimeBlock.objects.create(
            employee=self.employee,
            weekday=self.base_start.date().weekday(),
            start_time=time(16, 0),
            end_time=time(16, 15),
            label="Pausa semanal",
            date_from=self.base_start.date(),
        )
        self._auth(self.owner_user)

        response = self.api_client.get(reverse("mobile_api:calendar_day"), {"date": "2026-04-27"})

        self.assertEqual(response.status_code, 200)
        employee_payload = next(item for item in response.json()["employees"] if item["employee"]["id"] == self.employee.pk)
        blocks = employee_payload["time_blocks"]
        self.assertEqual(len(blocks), 2)
        self.assertIn(one_time.pk, {item["id"] for item in blocks if not item["is_recurring"]})
        recurring_entries = [item for item in blocks if item["is_recurring"]]
        self.assertEqual(recurring_entries[0]["recurring_id"], recurring.pk)
        self.assertTrue(recurring_entries[0]["editable"])

    def test_can_create_recurring_time_block_and_list_expanded_occurrence(self):
        self._auth(self.owner_user)

        create_response = self.api_client.post(
            reverse("mobile_api:time_blocks"),
            {
                "employee": self.employee.pk,
                "recurring": True,
                "weekday": 0,
                "start_time": "15:00",
                "end_time": "15:20",
                "date_from": "2026-04-01",
                "reason": "Pausa semanal",
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        self.assertTrue(create_response.json()["is_recurring"])

        list_response = self.api_client.get(
            reverse("mobile_api:time_blocks"),
            {"date": "2026-04-27", "employee": self.employee.pk},
        )
        self.assertEqual(list_response.status_code, 200)
        results = list_response.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["is_recurring"])
        self.assertEqual(results[0]["reason"], "Pausa semanal")

        patch_response = self.api_client.patch(
            reverse("mobile_api:time_block_detail", args=[results[0]["id"]]),
            {"end_time": "15:30", "reason": "Pausa ajustada"},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.json()["end_time"], "15:30:00")
        self.assertEqual(patch_response.json()["reason"], "Pausa ajustada")

        delete_response = self.api_client.delete(reverse("mobile_api:time_block_detail", args=[results[0]["id"]]))
        self.assertEqual(delete_response.status_code, 204)
        self.assertFalse(EmployeeRecurringTimeBlock.objects.exists())

    def test_availability_slots_respects_blocks_breaks_bookings_and_zone_conflicts(self):
        EmployeeWeeklyShift.objects.filter(employee=self.employee, weekday=0).update(
            break_start=time(13, 0),
            break_end=time(13, 30),
            break_label="Pausa",
        )
        self._create_booking(employee=self.employee, zone=self.zone, start_at=self.base_start)
        self._create_booking(
            employee=self.other_employee,
            client=self.other_client,
            zone=self.zone,
            start_at=self.base_start + timedelta(hours=2),
        )
        EmployeeTimeBlock.objects.create(
            employee=self.employee,
            date=self.base_start.date(),
            start_time=time(14, 0),
            end_time=time(14, 30),
            label="Personal",
        )
        EmployeeRecurringTimeBlock.objects.create(
            employee=self.employee,
            weekday=0,
            start_time=time(15, 0),
            end_time=time(15, 15),
            label="Recurrente",
            date_from=self.base_start.date(),
        )
        self._auth(self.owner_user)

        response = self.api_client.get(
            reverse("mobile_api:availability_slots"),
            {
                "date": "2026-04-27",
                "employee": self.employee.pk,
                "service": self.service.pk,
                "zone": self.zone.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["date"], "2026-04-27")
        self.assertEqual(payload["employee"], self.employee.pk)
        self.assertEqual(payload["service"], self.service.pk)
        self.assertEqual(payload["zone"], self.zone.pk)
        self.assertEqual(payload["duration"], 60)
        self.assertEqual(payload["step_minutes"], 15)

        labels = {slot["label"] for slot in payload["slots"]}
        self.assertIn("09:00", labels)
        self.assertIn("11:00", labels)
        self.assertNotIn("10:00", labels)
        self.assertNotIn("12:00", labels)
        self.assertNotIn("13:00", labels)
        self.assertNotIn("14:00", labels)
        self.assertNotIn("15:00", labels)

        reasons = {item["reason"] for item in payload["blocked"]}
        self.assertTrue({"Reserva", "Zona ocupada", "Pausa", "Personal", "Recurrente"}.issubset(reasons))

    def test_availability_slots_excludes_current_booking_for_reschedule(self):
        booking = self._create_booking(employee=self.employee, zone=self.zone, start_at=self.base_start)
        self._auth(self.owner_user)

        blocked_response = self.api_client.get(
            reverse("mobile_api:availability_slots"),
            {
                "date": "2026-04-27",
                "employee": self.employee.pk,
                "service": self.service.pk,
                "zone": self.zone.pk,
            },
        )
        self.assertEqual(blocked_response.status_code, 200)
        self.assertNotIn("10:00", {slot["label"] for slot in blocked_response.json()["slots"]})

        reschedule_response = self.api_client.get(
            reverse("mobile_api:availability_slots"),
            {
                "date": "2026-04-27",
                "employee": self.employee.pk,
                "service": self.service.pk,
                "zone": self.zone.pk,
                "booking": booking.pk,
            },
        )
        self.assertEqual(reschedule_response.status_code, 200)
        self.assertIn("10:00", {slot["label"] for slot in reschedule_response.json()["slots"]})
        self.assertNotIn("Reserva", {item["reason"] for item in reschedule_response.json()["blocked"]})

    def test_availability_slots_returns_spanish_validation_errors(self):
        self._auth(self.employee_user)

        missing_zone = self.api_client.get(
            reverse("mobile_api:availability_slots"),
            {
                "date": "2026-04-27",
                "employee": self.employee.pk,
                "service": self.service.pk,
            },
        )
        self.assertEqual(missing_zone.status_code, 200)
        self.assertIn("slots", missing_zone.json())

        team_employee = self.api_client.get(
            reverse("mobile_api:availability_slots"),
            {
                "date": "2026-04-27",
                "employee": self.other_employee.pk,
                "service": self.service.pk,
                "zone": self.zone.pk,
            },
        )
        self.assertEqual(team_employee.status_code, 200)
        self.assertIn("slots", team_employee.json())

    def test_booking_serializer_includes_payment_info(self):
        booking = self._create_booking(employee=self.employee, zone=self.zone)
        Payment.objects.create(
            booking=booking,
            amount=Decimal("15.00"),
            currency="eur",
            order_number="stripe-mobile-paid",
            provider=Payment.Providers.STRIPE,
            method=Payment.Methods.CARD,
            status=Payment.Statuses.PAID,
            paid_at=timezone.now(),
        )
        self._auth(self.owner_user)

        response = self.api_client.get(reverse("mobile_api:booking_detail", args=[booking.pk]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["payment_status"], Payment.Statuses.PAID)
        self.assertEqual(payload["paid_amount"], "15.00")
        self.assertIsNotNone(payload["latest_payment_id"])
        self.assertTrue(payload["can_pay"])

    @override_settings(STRIPE_SECRET_KEY="sk_test_mock", STRIPE_CURRENCY="eur", BOOKING_DEPOSIT_AMOUNT_EUR="12.00")
    @patch("payments.stripe_service.stripe.checkout.Session.create")
    def test_stripe_checkout_endpoint_returns_checkout_url(self, mocked_create):
        mocked_create.return_value = SimpleNamespace(
            id="cs_mobile",
            url="https://checkout.stripe.test/mobile",
            payment_intent="pi_mobile",
        )
        booking = self._create_booking(employee=self.employee, zone=self.zone)
        self._auth(self.owner_user)

        response = self.api_client.post(reverse("mobile_api:booking_stripe_checkout", args=[booking.pk]))

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["checkout_url"], "https://checkout.stripe.test/mobile")
        payment = Payment.objects.get(pk=payload["payment_id"])
        self.assertEqual(payment.amount, Decimal("12.00"))
        self.assertEqual(payment.stripe_checkout_session_id, "cs_mobile")
