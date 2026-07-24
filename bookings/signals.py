import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import Booking

logger = logging.getLogger(__name__)


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
