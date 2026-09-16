from unittest.mock import patch

from django.test import TestCase


class ApiHealthTests(TestCase):
    def test_public_database_health(self):
        response = self.client.get('/api/v1/health/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok', 'database': True})
        self.assertIn('no-store', response['Cache-Control'])

    def test_database_failure_hides_details(self):
        with patch('mobile_api.health.connection.cursor', side_effect=RuntimeError('secret')):
            response = self.client.get('/api/v1/health/')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b'secret', response.content)

    def test_read_only(self):
        self.assertEqual(self.client.post('/api/v1/health/').status_code, 405)
