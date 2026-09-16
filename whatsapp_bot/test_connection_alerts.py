import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from .connection_alerts import check_connection_alert


class ConnectionAlertTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.override = override_settings(BASE_DIR=Path(self.temp.name))
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.outage = {"status": "qr", "connected": False, "reconnect_url": "https://brimoon.es/whatsapp/connect/main/"}

    @patch("whatsapp_bot.connection_alerts.send_owner_alert", return_value=1)
    @patch("whatsapp_bot.connection_alerts.time.time")
    def test_alert_after_five_minutes_and_no_hourly_spam(self, clock, send):
        clock.return_value = 10000
        self.assertEqual(check_connection_alert(self.outage), 0)
        clock.return_value = 10301
        self.assertEqual(check_connection_alert(self.outage), 1)
        clock.return_value = 10601
        self.assertEqual(check_connection_alert(self.outage), 0)
        send.assert_called_once()
        check_connection_alert({**self.outage, "connected": True})
        clock.return_value = 10602
        self.assertEqual(check_connection_alert(self.outage), 0)

    @patch("whatsapp_bot.connection_alerts.send_owner_alert", return_value=0)
    @patch("whatsapp_bot.connection_alerts.time.time")
    def test_failed_delivery_is_retried_not_marked_sent(self, clock, send):
        clock.return_value = 10000
        check_connection_alert(self.outage)
        clock.return_value = 10301
        check_connection_alert(self.outage)
        clock.return_value = 10602
        check_connection_alert(self.outage)
        self.assertEqual(send.call_count, 2)
