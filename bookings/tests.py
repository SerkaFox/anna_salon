from datetime import date, datetime, time
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from bookings.forms import BookingForm
from clients.models import Client
from employees.models import Employee, EmployeeScheduleOverride, EmployeeWeeklyShift
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
