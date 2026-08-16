from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from clients.models import Client


class PasswordRecoveryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.api = APIClient()

    @patch("accounts.password_recovery._email_credentials", return_value=False)
    @patch("accounts.password_recovery.send_password_reset_credentials", return_value=True)
    def test_phone_recovery_creates_client_login(self, send_whatsapp, _send_email):
        client = Client.objects.create(
            first_name="Maria",
            phone="+34 600 111 222",
            is_active=True,
        )

        response = self.api.post(
            reverse("mobile_api:password_recovery"),
            {"identifier": "600111222"},
            format="json",
        )

        self.assertEqual(response.status_code, 202)
        client.refresh_from_db()
        self.assertIsNotNone(client.user)
        self.assertEqual(client.user.role, client.user.ROLE_CLIENT)
        call = send_whatsapp.call_args
        self.assertEqual(call.args[0], client)
        self.assertEqual(call.kwargs["username"], client.user.username)
        self.assertTrue(client.user.check_password(call.kwargs["password"]))

    @patch("accounts.password_recovery._email_credentials", return_value=False)
    @patch("accounts.password_recovery.send_password_reset_credentials", return_value=False)
    def test_password_is_not_changed_when_nothing_was_delivered(self, _whatsapp, _email):
        client = Client.objects.create(first_name="Ana", email="ana@example.com")

        response = self.api.post(
            reverse("mobile_api:password_recovery"),
            {"identifier": "ana@example.com"},
            format="json",
        )

        self.assertEqual(response.status_code, 202)
        client.refresh_from_db()
        self.assertIsNone(client.user)

    def test_unknown_identity_has_same_public_response(self):
        response = self.api.post(
            reverse("mobile_api:password_recovery"),
            {"identifier": "missing@example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, 202)
        self.assertIn("message", response.json())
