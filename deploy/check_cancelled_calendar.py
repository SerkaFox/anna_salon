"""Read-only smoke test of the deployed calendar API using existing owner."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'anna_core.settings')
import django
django.setup()

from accounts.models import User
from bookings.models import Booking
from django.utils import timezone
from mobile_api.views import CalendarDayView
from rest_framework.test import APIRequestFactory, force_authenticate

owner = User.objects.filter(role=User.ROLE_OWNER, is_active=True).first()
assert owner is not None
sample = Booking.objects.filter(status=Booking.Statuses.CANCELLED).order_by('-start_at').first()
date = timezone.localtime(sample.start_at).date() if sample else timezone.localdate()
factory = APIRequestFactory()
for period in (None, 'all', 'today', 'week'):
    params = {'date': date.isoformat()}
    if period:
        params['cancelled'] = period
    request = factory.get('/api/v1/calendar/day/', params, HTTP_HOST='brimoon.es')
    force_authenticate(request, user=owner)
    response = CalendarDayView.as_view()(request)
    assert response.status_code == 200, response.status_code
    rows = response.data['bookings']
    assert all((row['status'] == 'cancelled') == bool(period) for row in rows)
    if sample and period == 'all':
        assert sample.pk in [row['id'] for row in rows]
    print('Calendar mode', period or 'normal', ': OK, records:', len(rows))
