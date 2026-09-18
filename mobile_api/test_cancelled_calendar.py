from datetime import datetime, time, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bookings.models import Booking
from .tests import MobileApiMvpTests


class CancelledCalendarTests(TestCase):
    setUp = MobileApiMvpTests.setUp
    _ensure_weekly_shifts = MobileApiMvpTests._ensure_weekly_shifts
    _create_booking = MobileApiMvpTests._create_booking

    def calendar(self, **params):
        params.setdefault('date', self.base_start.date().isoformat())
        return self.api_client.get(reverse('mobile_api:calendar_day'), params)

    def test_normal_and_cancelled_modes_are_separate(self):
        self.api_client.force_authenticate(self.owner_user)
        active = self._create_booking()
        cancelled = self._create_booking(status=Booking.Statuses.CANCELLED)
        self.assertEqual([b['id'] for b in self.calendar().data['bookings']], [active.pk])
        response = self.calendar(cancelled='all')
        self.assertEqual([b['id'] for b in response.data['bookings']], [cancelled.pk])
        self.assertFalse(response.data['bookings'][0]['cancellation_date_is_estimated'])
        self.assertEqual(response.data['employees'], self.calendar().data['employees'])

    def test_filters_use_cancellation_date_not_visit_or_latest_edit(self):
        self.api_client.force_authenticate(self.owner_user)
        recent = self._create_booking(status=Booking.Statuses.CANCELLED)
        old = self._create_booking(status=Booking.Statuses.CANCELLED)
        Booking.objects.filter(pk=old.pk).update(cancelled_at=timezone.now() - timedelta(days=20))
        self.assertEqual([b['id'] for b in self.calendar(cancelled='today').data['bookings']], [recent.pk])
        self.assertEqual([b['id'] for b in self.calendar(cancelled='week').data['bookings']], [recent.pk])
        remote = self._create_booking(start_at=self.base_start + timedelta(days=40), status=Booking.Statuses.CANCELLED)
        dates = self.calendar(cancelled='today').data['cancellation_dates']
        self.assertIn({'date': remote.start_at.date().isoformat(), 'count': 1}, dates)

    def test_legacy_date_is_marked_estimated(self):
        self.api_client.force_authenticate(self.owner_user)
        legacy = self._create_booking(status=Booking.Statuses.CANCELLED)
        Booking.objects.filter(pk=legacy.pk).update(cancelled_at=None)
        self.assertTrue(self.calendar(cancelled='all').data['bookings'][0]['cancellation_date_is_estimated'])

    def test_validation_and_client_permissions(self):
        self.api_client.force_authenticate(self.owner_user)
        self.assertEqual(self.calendar(cancelled='bad').status_code, 400)
        self.owner_user.role = 'client'
        self.owner_user.save(update_fields=['role'])
        self.assertEqual(self.calendar(cancelled='all').status_code, 403)

    def test_timestamp_tracks_transition_and_survives_later_edits(self):
        booking = self._create_booking()
        self.assertIsNone(booking.cancelled_at)
        booking.status = Booking.Statuses.CANCELLED
        booking.save(update_fields=['status', 'updated_at'])
        moment = booking.cancelled_at
        booking.notes = 'changed later'
        booking.save(update_fields=['notes', 'updated_at'])
        booking.refresh_from_db()
        self.assertEqual(booking.cancelled_at, moment)
        booking.status = Booking.Statuses.CONFIRMED
        booking.save(update_fields=['status'])
        booking.refresh_from_db()
        self.assertIsNone(booking.cancelled_at)

    def test_restore_preserves_order_and_prevents_duplicate_restoration(self):
        self.api_client.force_authenticate(self.owner_user)
        booking = self._create_booking(status=Booking.Statuses.CANCELLED)
        start = timezone.now().replace(hour=22, minute=0, second=0, microsecond=0) + timedelta(days=2)
        snapshots = (booking.duration_snapshot, booking.client_price_snapshot, booking.service_items_snapshot)
        response = self.api_client.post(reverse('mobile_api:booking_restore', args=[booking.pk]), {'employee': booking.employee_id, 'start_at': start.isoformat()}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Statuses.CONFIRMED)
        self.assertIsNone(booking.cancelled_at)
        self.assertIsNone(booking.prepayment_deadline_at)
        self.assertEqual(snapshots, (booking.duration_snapshot, booking.client_price_snapshot, booking.service_items_snapshot))
        response = self.api_client.post(reverse('mobile_api:booking_restore', args=[booking.pk]), {'employee': booking.employee_id, 'start_at': start.isoformat()}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_restore_conflict_and_client_denied(self):
        self.api_client.force_authenticate(self.owner_user)
        start = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=3)
        self._create_booking(start_at=start)
        booking = self._create_booking(status=Booking.Statuses.CANCELLED)
        response = self.api_client.post(reverse('mobile_api:booking_restore', args=[booking.pk]), {'employee': booking.employee_id, 'start_at': start.isoformat()}, format='json')
        self.assertEqual(response.status_code, 400)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Statuses.CANCELLED)
        self.owner_user.role = 'client'
        self.owner_user.save(update_fields=['role'])
        response = self.api_client.post(reverse('mobile_api:booking_restore', args=[booking.pk]), {'employee': booking.employee_id, 'start_at': start.isoformat()}, format='json')
        self.assertEqual(response.status_code, 403)

    def test_restore_archives_old_reminders_without_erasing_delivery_history(self):
        from whatsapp_bot.models import WhatsAppConnection, WhatsAppMessage
        self.api_client.force_authenticate(self.owner_user)
        booking = self._create_booking(status=Booking.Statuses.CANCELLED)
        message = WhatsAppMessage.objects.create(connection=WhatsAppConnection.objects.get_or_create(name='main')[0], booking=booking, client=booking.client, kind=WhatsAppMessage.Kinds.REMINDER_24H, to_phone='34600000000', body='Old visit', status=WhatsAppMessage.Statuses.SENT, sent_at=timezone.now())
        start = timezone.now().replace(hour=22, minute=0, second=0, microsecond=0) + timedelta(days=2)
        response = self.api_client.post(reverse('mobile_api:booking_restore', args=[booking.pk]), {'employee': booking.employee_id, 'start_at': start.isoformat()}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        message.refresh_from_db()
        self.assertIsNone(message.booking_id)
        self.assertEqual(message.status, WhatsAppMessage.Statuses.SENT)
        self.assertEqual(message.body, 'Old visit')

    def test_restore_rejects_past_time(self):
        self.api_client.force_authenticate(self.owner_user)
        booking = self._create_booking(status=Booking.Statuses.CANCELLED)
        response = self.api_client.post(reverse('mobile_api:booking_restore', args=[booking.pk]), {'employee': booking.employee_id, 'start_at': self.base_start.isoformat()}, format='json')
        self.assertEqual(response.status_code, 400)
