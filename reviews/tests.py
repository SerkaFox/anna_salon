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

from .models import ClientReview


class ClientReviewPortalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="client-review",
            password="testpass123",
            role=User.ROLE_CLIENT,
        )
        self.client_obj = Client.objects.create(
            user=self.user,
            first_name="Maria",
            phone="",
        )
        self.service = Service.objects.create(
            name="Manicura",
            duration_minutes=60,
            price=Decimal("30.00"),
            is_active=True,
        )
        self.employee = Employee.objects.create(first_name="Lucia", is_active=True)
        start_at = timezone.now() - timedelta(hours=2)
        self.booking = Booking.objects.create(
            client=self.client_obj,
            employee=self.employee,
            service=self.service,
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            status=Booking.Statuses.DONE,
            source=Booking.Sources.WEBSITE,
            price_snapshot=self.service.price,
            duration_snapshot=60,
            original_client_price_snapshot=self.service.price,
            client_price_snapshot=self.service.price,
            employee_percent_snapshot=Decimal("40.00"),
            employee_amount_snapshot=Decimal("12.00"),
            salon_amount_snapshot=Decimal("18.00"),
        )
        self.url = reverse("clients:booking_review", args=[self.booking.pk])

    def test_review_form_requires_client_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_client_can_review_only_completed_own_booking(self):
        self.client.force_login(self.user)

        response = self.client.post(
            self.url,
            {"rating": "5", "text": "Muy buen servicio."},
        )

        self.assertRedirects(response, reverse("clients:portal"))
        review = ClientReview.objects.get(booking=self.booking)
        self.assertEqual(review.client, self.client_obj)
        self.assertEqual(review.rating, 5)
        self.assertContains(self.client.get(reverse("clients:portal")), "Mis opiniones")

    def test_other_client_cannot_open_review(self):
        other_user = User.objects.create_user(
            username="other-review",
            password="testpass123",
            role=User.ROLE_CLIENT,
        )
        Client.objects.create(user=other_user, first_name="Other")
        self.client.force_login(other_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 404)
