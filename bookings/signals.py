import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import Booking

logger = logging.getLogger(__name__)


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
