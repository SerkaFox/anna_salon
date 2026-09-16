import importlib.util
from pathlib import Path
import sys
import types
import unittest
from urllib.error import HTTPError

if sys.platform == 'win32':
    sys.modules.setdefault('fcntl', types.ModuleType('fcntl'))
spec = importlib.util.spec_from_file_location('watchdog', Path(__file__).with_name('brimoon_watchdog.py'))
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


class WatchdogTests(unittest.TestCase):
    def test_confirm_failure_and_retry_unsent(self):
        first, message = watchdog.transition({}, (False, 'down'), 100)
        self.assertIsNone(message)
        second, message = watchdog.transition(first, (False, 'down'), 160)
        self.assertIn('Ошибка', message)
        _, retry = watchdog.transition(second, (False, 'down'), 220)
        self.assertIsNotNone(retry)

    def test_reminder_and_recovery(self):
        old = {'failures': 2, 'alerted': True, 'sent': 160}
        _, message = watchdog.transition(old, (False, 'down'), 220)
        self.assertIsNone(message)
        _, message = watchdog.transition(old, (False, 'down'), 3760)
        self.assertIsNotNone(message)
        item, message = watchdog.transition(old, (True, 'up'), 220)
        self.assertEqual(item['failures'], 0)
        self.assertIn('Восстановлено', message)

    def test_no_false_recovery_without_delivered_alert(self):
        _, message = watchdog.transition({'failures': 1}, (True, 'up'), 220)
        self.assertIsNone(message)

    def test_errors_do_not_leak_urls(self):
        def failing():
            raise OSError('secret token url')
        self.assertEqual(watchdog.probe(failing), (False, 'OSError'))

    def test_http_status_without_sensitive_url(self):
        def failing():
            raise HTTPError('secret', 503, 'unavailable', {}, None)
        self.assertEqual(watchdog.probe(failing), (False, 'HTTP 503'))


if __name__ == '__main__':
    unittest.main()
