import logging
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from bookings.models import Booking
from .models import PushDevice

logger = logging.getLogger(__name__)


def _firebase_app():
    credentials_file = getattr(settings, "FIREBASE_CREDENTIALS_FILE", "")
    if not credentials_file:
        return None

    try:
        import firebase_admin
        from firebase_admin import credentials

        try:
            return firebase_admin.get_app()
        except ValueError:
            options = {}
            project_id = getattr(settings, "FIREBASE_PROJECT_ID", "")
            if project_id:
                options["projectId"] = project_id
            return firebase_admin.initialize_app(
                credentials.Certificate(str(Path(credentials_file))),
                options or None,
            )
    except Exception:
        logger.exception("Firebase could not be initialized.")
        return None


def _notification_text(booking, locale):
    local_start = timezone.localtime(booking.start_at)
    client_name = booking.client.full_name or str(booking.client)
    service_names = booking.service_names
    if locale == PushDevice.Locales.RUSSIAN:
        return (
            "Новая запись",
            f"{client_name} · {local_start:%d.%m.%Y} в {local_start:%H:%M} · {service_names}",
        )
    return (
        "Nueva reserva",
        f"{client_name} · {local_start:%d/%m/%Y} a las {local_start:%H:%M} · {service_names}",
    )


def send_new_booking_notification(booking_id):
    app = _firebase_app()
    if app is None:
        return 0

    try:
        from firebase_admin import messaging

        booking = Booking.objects.select_related(
            "client", "employee", "employee__user", "service"
        ).get(pk=booking_id)
    except Booking.DoesNotExist:
        return 0

    if (
        booking.status == Booking.Statuses.CANCELLED
        or booking.start_at < timezone.now() - timedelta(minutes=5)
        or not booking.employee.user_id
    ):
        return 0

    devices = PushDevice.objects.filter(is_active=True).filter(
        Q(user_id=booking.employee.user_id)
        | Q(user__role__in=("owner", "admin"))
    )
    sent = 0
    for device in devices:
        title, body = _notification_text(booking, device.locale)
        message = messaging.Message(
            token=device.registration_token,
            notification=messaging.Notification(title=title, body=body),
            data={
                "type": "new_booking",
                "booking_id": str(booking.pk),
                "booking_date": timezone.localtime(booking.start_at).date().isoformat(),
            },
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    channel_id="bookings",
                    sound="default",
                ),
            ),
        )
        try:
            messaging.send(message, app=app)
            sent += 1
        except Exception as exc:
            code = getattr(exc, "code", "")
            if code in {
                "registration-token-not-registered",
                "invalid-registration-token",
                "installation-id-not-registered",
            }:
                PushDevice.objects.filter(pk=device.pk).update(is_active=False)
            else:
                logger.exception(
                    "Could not send booking %s push notification to device %s.",
                    booking.pk,
                    device.pk,
                )
    return sent
