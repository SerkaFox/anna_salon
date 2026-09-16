import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse

from . import bridge
from .models import WhatsAppConnection


@override_settings(WHATSAPP_CONNECT_PIN="1234", WHATSAPP_CONNECTION_NAME="main")
class ConnectAccessTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.override = override_settings(BASE_DIR=Path(self.temp.name))
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.url = reverse("whatsapp_bot:connect", args=["main"])

    @patch("whatsapp_bot.views.bridge.get_qr")
    def test_password_required_without_login(self, get_qr):
        response = self.client.get(self.url)
        self.assertContains(response, "Contraseña")
        self.assertNotIn("login", response.content.decode().lower().split('<form')[1].split('</form>')[0].replace('login-block', ''))
        get_qr.assert_not_called()
        self.assertIn("no-store", response["Cache-Control"])

    @patch("whatsapp_bot.views.bridge.get_qr")
    def test_customer_login_does_not_bypass_password(self, get_qr):
        user = get_user_model().objects.create_user(username="access-test", password="secret")
        self.client.force_login(user)
        self.assertContains(self.client.get(self.url), "Contraseña")
        get_qr.assert_not_called()

    @patch("whatsapp_bot.views.bridge.get_qr", return_value={"status": "ready", "phone": "34600000000"})
    def test_password_grants_access_and_status_is_normalized(self, get_qr):
        response = self.client.post(self.url, {"pin": "1234"})
        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.get(self.url), "Conectado")
        self.assertEqual(WhatsAppConnection.objects.get(name="main").status, "connected")

    def test_other_sessions_not_accessible(self):
        self.assertEqual(self.client.get(reverse("whatsapp_bot:connect", args=["other"])).status_code, 404)

    def test_pairing_progress_requires_password(self):
        response = self.client.get(reverse('whatsapp_bot:pairing_progress', args=['main']))
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(b'code_at', response.content)

    @patch('whatsapp_bot.views.bridge.pairing_progress', return_value={'status': 'pairing', 'auth_mode': 'code', 'code': 'TEST-CODE', 'code_at': 1000, 'phone': 'private'})
    def test_pairing_progress_returns_live_code_without_private_data(self, progress):
        self.client.post(self.url, {'pin': '1234'})
        response = self.client.get(reverse('whatsapp_bot:pairing_progress', args=['main']))
        self.assertEqual(response.json()['code'], 'TEST-CODE')
        self.assertNotIn('phone', response.json())
        self.assertIn('no-store', response['Cache-Control'])

    @patch('whatsapp_bot.views.bridge.pairing_progress', return_value={'status': 'pairing', 'code': 'LIVE-CODE'})
    def test_pairing_page_uses_current_code(self, progress):
        self.client.post(self.url, {'pin': '1234'})
        response = self.client.get(reverse('whatsapp_bot:pairing_code', args=['main']))
        self.assertContains(response, 'LIVE-CODE')
        self.assertContains(response, 'pairing-progress')

    @patch('whatsapp_bot.views.bridge.get_qr', return_value={'status': 'pairing', 'auth_mode': 'code', 'qr': 'ignored'})
    def test_code_mode_page_does_not_show_qr(self, get_qr):
        self.client.post(self.url, {'pin': '1234'})
        response = self.client.get(self.url)
        self.assertContains(response, 'Vinculación por número y código')
        self.assertNotContains(response, '<img')

    def test_qr_is_protected_and_legacy_grant_rejected(self):
        session = self.client.session
        session["wa_connect_auth_main"] = True
        session.save()
        self.assertEqual(self.client.get(reverse("whatsapp_bot:qr_image", args=["main"])).status_code, 403)

    def test_expired_password_access_is_rejected(self):
        session = self.client.session
        session["wa_connect_auth_main"] = time.time() - 1801
        session.save()
        self.assertContains(self.client.get(self.url), "Contraseña")

    def test_five_wrong_attempts_are_throttled(self):
        for _ in range(5):
            self.client.post(self.url, {"pin": "bad"})
        self.assertContains(self.client.post(self.url, {"pin": "1234"}), "cinco minutos")

    @patch("whatsapp_bot.views.bridge.request_pairing_code", side_effect=bridge.WhatsAppBridgeError("timeout"))
    def test_pairing_timeout_has_friendly_error_not_500(self, request_code):
        self.client.post(self.url, {"pin": "1234"})
        response = self.client.post(reverse("whatsapp_bot:pairing_code", args=["main"]), {"phone": "34600000000"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No se ha borrado")

    @patch("whatsapp_bot.views.bridge.cancel_pairing")
    def test_return_to_qr_requires_post_and_password(self, cancel):
        url = reverse("whatsapp_bot:return_to_qr", args=["main"])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url)
        cancel.assert_not_called()
        self.client.post(self.url, {"pin": "1234"})
        self.client.post(url)
        cancel.assert_called_once()


class BridgeTimeoutTests(SimpleTestCase):
    @override_settings(WHATSAPP_BRIDGE_URL="http://127.0.0.1:8125", WHATSAPP_BRIDGE_TOKEN="")
    @patch("whatsapp_bot.bridge.urlopen", side_effect=TimeoutError("timed out"))
    def test_timeout_is_domain_error(self, urlopen):
        with self.assertRaises(bridge.WhatsAppBridgeError):
            bridge._request("/health")
