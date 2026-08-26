import logging
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from bookings.models import Booking
from employees.models import Employee
from .models import PushDevice

logger = logging.getLogger(__name__)

EVENT_NEW_BOOKING = "new_booking"
EVENT_BOOKING_CANCELLED = "booking_cancelled"
EVENT_BOOKING_RESCHEDULED = "booking_rescheduled"
EVENT_EMPLOYEE_CHANGED = "employee_changed"
EVENT_PREPAYMENT_RECEIVED = "prepayment_received"
EVENT_REMINDER_24H = "reminder_24h"
EVENT_REMINDER_2H = "reminder_2h"

PREFERENCE_FIELDS = {
    EVENT_NEW_BOOKING: "notify_new_booking",
    EVENT_BOOKING_CANCELLED: "notify_booking_cancelled",
    EVENT_BOOKING_RESCHEDULED: "notify_booking_rescheduled",
    EVENT_EMPLOYEE_CHANGED: "notify_employee_changed",
    EVENT_PREPAYMENT_RECEIVED: "notify_prepayment_received",
    EVENT_REMINDER_24H: "notify_reminder_24h",
    EVENT_REMINDER_2H: "notify_reminder_2h",
}


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


def _notification_text(booking, locale, event_type=EVENT_NEW_BOOKING, context=None):
    context = context or {}
    local_start = timezone.localtime(booking.start_at)
    client_name = booking.client.full_name or str(booking.client)
    service_names = booking.service_names
    if locale == PushDevice.Locales.RUSSIAN:
        details = f"{client_name} · {local_start:%d.%m.%Y} в {local_start:%H:%M} · {service_names}"
        if event_type == EVENT_BOOKING_CANCELLED:
            return "Запись отменена", details
        if event_type == EVENT_BOOKING_RESCHEDULED:
            old_start = context.get("old_start_at")
            old_text = (
                timezone.localtime(old_start).strftime("%d.%m в %H:%M")
                if old_start
                else "прежнего времени"
            )
            return "Запись перенесена", f"{client_name} · с {old_text} на {local_start:%d.%m в %H:%M}"
        if event_type == EVENT_EMPLOYEE_CHANGED:
            return "Смена мастера", f"{details} · теперь {booking.employee.full_name}"
        if event_type == EVENT_PREPAYMENT_RECEIVED:
            amount = context.get("amount", "")
            return "Предоплата получена", f"{details} · оплачено {amount} €"
        if event_type == EVENT_REMINDER_24H:
            return "Запись завтра", details
        if event_type == EVENT_REMINDER_2H:
            return "Клиент через 2 часа", details
        return "Новая запись", details

    details = f"{client_name} · {local_start:%d/%m/%Y} a las {local_start:%H:%M} · {service_names}"
    if event_type == EVENT_BOOKING_CANCELLED:
        return "Reserva cancelada", details
    if event_type == EVENT_BOOKING_RESCHEDULED:
        old_start = context.get("old_start_at")
        old_text = (
            timezone.localtime(old_start).strftime("%d/%m a las %H:%M")
            if old_start
            else "la hora anterior"
        )
        return "Reserva reprogramada", f"{client_name} · de {old_text} a {local_start:%d/%m a las %H:%M}"
    if event_type == EVENT_EMPLOYEE_CHANGED:
        return "Cambio de especialista", f"{details} · ahora {booking.employee.full_name}"
    if event_type == EVENT_PREPAYMENT_RECEIVED:
        amount = context.get("amount", "")
        return "Prepago recibido", f"{details} · pagado {amount} €"
    if event_type == EVENT_REMINDER_24H:
        return "Reserva mañana", details
    if event_type == EVENT_REMINDER_2H:
        return "Cliente en 2 horas", details
    return "Nueva reserva", details


def send_booking_notification(
    booking_id,
    event_type=EVENT_NEW_BOOKING,
    *,
    previous_state=None,
    context=None,
):
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

    if event_type != EVENT_BOOKING_CANCELLED and booking.status == Booking.Statuses.CANCELLED:
        return 0
    if (
        event_type == EVENT_NEW_BOOKING
        and booking.start_at < timezone.now() - timedelta(minutes=5)
    ):
        return 0

    recipients = Q(user__role__in=("owner", "admin"))
    if booking.employee.user_id:
        recipients |= Q(user_id=booking.employee.user_id)
    if event_type == EVENT_EMPLOYEE_CHANGED and previous_state:
        previous_employee = Employee.objects.filter(
            pk=previous_state.get("employee_id")
        ).first()
        if previous_employee and previous_employee.user_id:
            recipients |= Q(user_id=previous_employee.user_id)
    preference_field = PREFERENCE_FIELDS[event_type]
    devices = PushDevice.objects.filter(is_active=True).filter(recipients).filter(
        **{preference_field: True}
    )
    sent = 0
    for device in devices:
        title, body = _notification_text(
            booking,
            device.locale,
            event_type,
            {**(context or {}), **(previous_state or {})},
        )
        message = messaging.Message(
            token=device.registration_token,
            notification=messaging.Notification(title=title, body=body),
            data={
                "type": event_type,
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


def send_new_booking_notification(booking_id):
    return send_booking_notification(booking_id, EVENT_NEW_BOOKING)
