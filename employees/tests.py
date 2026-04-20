from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from bookings.models import Booking
from clients.models import Client
from employees.models import Employee
from services_app.models import Service


class EmployeeListAnalyticsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="testpass123")
        self.client.force_login(self.user)

        self.service_cut = Service.objects.create(
            name="Corte",
            price=Decimal("50.00"),
            duration_minutes=60,
        )
        self.service_color = Service.objects.create(
            name="Color",
            price=Decimal("90.00"),
            duration_minutes=90,
        )

        self.employee_anna = Employee.objects.create(first_name="Anna", last_name="Ruiz", is_active=True)
        self.employee_lia = Employee.objects.create(first_name="Lia", last_name="Costa", is_active=True)
        self.employee_anna.services.add(self.service_cut, self.service_color)
        self.employee_lia.services.add(self.service_cut)

        self.client_maria = Client.objects.create(first_name="Maria", last_name="Lopez")
        self.client_sofia = Client.objects.create(first_name="Sofia", last_name="Diaz")
        self.client_olga = Client.objects.create(first_name="Olga", last_name="Marin")

        now = timezone.now()
        self._create_booking(
            employee=self.employee_anna,
            client=self.client_maria,
            service=self.service_cut,
            start_at=now,
            client_price=Decimal("50.00"),
            employee_amount=Decimal("20.00"),
            salon_amount=Decimal("30.00"),
        )
        self._create_booking(
            employee=self.employee_anna,
            client=self.client_maria,
            service=self.service_color,
            start_at=now + timedelta(hours=2),
            client_price=Decimal("90.00"),
            employee_amount=Decimal("36.00"),
            salon_amount=Decimal("54.00"),
        )
        self._create_booking(
            employee=self.employee_anna,
            client=self.client_sofia,
            service=self.service_color,
            start_at=now + timedelta(days=1),
            client_price=Decimal("90.00"),
            employee_amount=Decimal("36.00"),
            salon_amount=Decimal("54.00"),
        )
        self._create_booking(
            employee=self.employee_lia,
            client=self.client_olga,
            service=self.service_cut,
            start_at=now + timedelta(days=2),
            client_price=Decimal("50.00"),
            employee_amount=Decimal("15.00"),
            salon_amount=Decimal("35.00"),
        )
        self._create_booking(
            employee=self.employee_lia,
            client=self.client_olga,
            service=self.service_cut,
            start_at=now + timedelta(days=3),
            client_price=Decimal("50.00"),
            employee_amount=Decimal("15.00"),
            salon_amount=Decimal("35.00"),
            status=Booking.Statuses.CANCELLED,
        )

    def _create_booking(
        self,
        *,
        employee,
        client,
        service,
        start_at,
        client_price,
        employee_amount,
        salon_amount,
        status=Booking.Statuses.DONE,
    ):
        return Booking.objects.create(
            employee=employee,
            client=client,
            service=service,
            start_at=start_at,
            end_at=start_at + timedelta(minutes=service.duration_minutes),
            status=status,
            price_snapshot=client_price,
            duration_snapshot=service.duration_minutes,
            original_client_price_snapshot=client_price,
            client_price_snapshot=client_price,
            discount_amount_snapshot=Decimal("0.00"),
            employee_percent_snapshot=Decimal("40.00"),
            employee_amount_snapshot=employee_amount,
            salon_amount_snapshot=salon_amount,
        )

    def test_employee_list_includes_money_client_and_service_analytics(self):
        response = self.client.get(reverse("employees:list"))

        self.assertEqual(response.status_code, 200)
        employees = response.context["employees"]
        anna = next(employee for employee in employees if employee.pk == self.employee_anna.pk)
        lia = next(employee for employee in employees if employee.pk == self.employee_lia.pk)

        self.assertEqual(anna.employee_earnings, Decimal("92.00"))
        self.assertEqual(anna.client_revenue, Decimal("230.00"))
        self.assertEqual(anna.salon_revenue, Decimal("138.00"))
        self.assertEqual(anna.bookings_count, 3)
        self.assertEqual(anna.clients_count, 2)
        self.assertEqual(anna.repeat_clients_count, 1)
        self.assertEqual(anna.top_clients[0]["name"], self.client_maria.full_name)
        self.assertEqual(anna.top_clients[0]["count"], 2)
        self.assertEqual(anna.top_services[0]["name"], self.service_color.name)
        self.assertEqual(anna.top_services[0]["count"], 2)

        self.assertEqual(lia.employee_earnings, Decimal("15.00"))
        self.assertEqual(lia.bookings_count, 1)
        self.assertEqual(lia.clients_count, 1)

        self.assertContains(response, "Ganado")
        self.assertContains(response, "Clientes frecuentes")
        self.assertContains(response, "Servicios más demandados")

    def test_employee_list_can_sort_by_clients(self):
        response = self.client.get(reverse("employees:list"), {"sort": "clients_desc"})

        self.assertEqual(response.status_code, 200)
        employees = list(response.context["employees"])

        self.assertEqual(employees[0].pk, self.employee_anna.pk)
        self.assertEqual(employees[1].pk, self.employee_lia.pk)
        self.assertEqual(employees[0].clients_rank, 1)
        self.assertEqual(employees[0].revenue_rank, 1)
