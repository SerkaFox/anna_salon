from decimal import Decimal

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import User
from clients.models import Client

from clients.management.commands.import_treatwell_customers import (
    _repair_swapped_contact_fields,
)


class TreatwellContactRepairTests(SimpleTestCase):
    def test_swaps_phone_exported_as_name_with_name_exported_as_phone(self):
        self.assertEqual(
            _repair_swapped_contact_fields("691019909", "Eztizen"),
            ("Eztizen", "691019909"),
        )

    def test_moves_phone_out_of_name_when_exported_name_is_missing(self):
        self.assertEqual(
            _repair_swapped_contact_fields("654581713", ""),
            ("Cliente Treatwell", "654581713"),
        )

    def test_keeps_normal_contact_fields(self):
        self.assertEqual(
            _repair_swapped_contact_fields("Elena", "+34654581713"),
            ("Elena", "+34654581713"),
        )


class ClientAdminWebTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="owner-web",
            password="testpass123",
            role=User.ROLE_OWNER,
        )
        self.client.force_login(self.user)

    def test_client_list_filters_blacklist_and_exposes_rank_values(self):
        blocked = Client.objects.create(
            first_name="Blocked",
            is_blacklisted=True,
            booking_count=3,
            average_expense_amount_cents=2500,
        )
        Client.objects.create(first_name="Visible")

        response = self.client.get(
            reverse("clients:list"),
            {"filter": "blacklisted", "sort": "spent"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item.pk for item in response.context["clients"]], [blocked.pk])
        self.assertEqual(response.context["clients"][0].total_orders, 3)
        self.assertEqual(response.context["clients"][0].total_spent, Decimal("75.00"))
        self.assertContains(response, "Lista negra")

    def test_blacklisted_client_cannot_submit_portal_booking(self):
        client_user = User.objects.create_user(
            username="blocked-client",
            password="testpass123",
            role=User.ROLE_CLIENT,
        )
        Client.objects.create(
            user=client_user,
            first_name="Blocked",
            is_blacklisted=True,
        )
        self.client.force_login(client_user)

        response = self.client.post(reverse("clients:portal"), {})

        self.assertRedirects(response, reverse("clients:portal"))

    def test_client_portal_uses_mobile_app_navigation(self):
        client_user = User.objects.create_user(
            username="portal-client",
            password="testpass123",
            role=User.ROLE_CLIENT,
        )
        Client.objects.create(user=client_user, first_name="Portal")
        self.client.force_login(client_user)

        response = self.client.get(reverse("clients:portal"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-client-tab="home"')
        self.assertContains(response, 'data-client-tab="booking"')
        self.assertContains(response, 'data-client-tab="settings"')
        self.assertContains(response, 'data-client-view="home"')
        self.assertContains(response, 'data-client-view="booking"')
        self.assertContains(response, 'data-client-view="settings"')
        self.assertContains(response, "Esta semana")
        self.assertContains(response, "Este mes")
        self.assertNotContains(response, "Gastado")
        self.assertNotContains(response, "Premios")
