import logging

from django.db import transaction
from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver
from django.utils import timezone

from .models import Booking, BookingPrepayment

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Booking)
def remember_booking_state_for_push(sender, instance, **kwargs):
    if not instance.pk:
        return
    instance._push_previous_state = (
        Booking.objects.filter(pk=instance.pk)
        .values("status", "start_at", "employee_id")
        .first()
    )


@receiver(post_save, sender=Booking)
def schedule_new_booking_push(sender, instance, created, **kwargs):
    if not created:
        return

    booking_id = instance.pk

    def send_push():
        from mobile_api.push_notifications import send_new_booking_notification

        try:
            send_new_booking_notification(booking_id)
        except Exception:
            logger.exception(
                "Could not send new booking push notification for booking %s.",
                booking_id,
            )

    transaction.on_commit(send_push)


@receiver(post_save, sender=Booking)
def schedule_booking_change_push(sender, instance, created, **kwargs):
    if created:
        return
    previous = getattr(instance, "_push_previous_state", None)
    if not previous:
        return

    from mobile_api.push_notifications import (
        EVENT_BOOKING_CANCELLED,
        EVENT_BOOKING_RESCHEDULED,
        EVENT_EMPLOYEE_CHANGED,
        send_booking_notification,
    )

    event_type = None
    if (
        instance.status == Booking.Statuses.CANCELLED
        and previous["status"] != Booking.Statuses.CANCELLED
    ):
        event_type = EVENT_BOOKING_CANCELLED
    elif instance.employee_id != previous["employee_id"]:
        event_type = EVENT_EMPLOYEE_CHANGED
    elif instance.start_at != previous["start_at"]:
        event_type = EVENT_BOOKING_RESCHEDULED
    if event_type is None:
        return

    booking_id = instance.pk

    def send_push():
        try:
            send_booking_notification(
                booking_id,
                event_type,
                previous_state=previous,
            )
        except Exception:
            logger.exception(
                "Could not send %s push notification for booking %s.",
                event_type,
                booking_id,
            )

    transaction.on_commit(send_push)


@receiver(pre_delete, sender=Booking)
def send_deleted_booking_push(sender, instance, **kwargs):
    from mobile_api.push_notifications import (
        EVENT_BOOKING_CANCELLED,
        send_booking_notification,
    )

    try:
        send_booking_notification(instance.pk, EVENT_BOOKING_CANCELLED)
    except Exception:
        logger.exception(
            "Could not send cancellation push before deleting booking %s.",
            instance.pk,
        )


@receiver(post_save, sender=BookingPrepayment)
def schedule_prepayment_push(sender, instance, created, **kwargs):
    if not created or instance.status != BookingPrepayment.Statuses.PAID:
        return
    booking_id = instance.booking_id
    amount = str(instance.amount)

    def send_push():
        from mobile_api.push_notifications import (
            EVENT_PREPAYMENT_RECEIVED,
            send_booking_notification,
        )

        try:
            send_booking_notification(
                booking_id,
                EVENT_PREPAYMENT_RECEIVED,
                context={"amount": amount},
            )
        except Exception:
            logger.exception(
                "Could not send prepayment push for booking %s.",
                booking_id,
            )

    transaction.on_commit(send_push)


@receiver(post_save, sender=Booking)
def schedule_review_request(sender, instance, **kwargs):
    if instance.status != Booking.Statuses.DONE:
        return
    if not instance.completed_at:
        instance.completed_at = timezone.now()
        Booking.objects.filter(pk=instance.pk, completed_at__isnull=True).update(
            completed_at=instance.completed_at
        )
    from whatsapp_bot.services import queue_review_request

    try:
        queue_review_request(instance)
    except Exception:
        logger.exception("Could not queue review request for booking %s.", instance.pk)
