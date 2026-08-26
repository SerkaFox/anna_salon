from django.conf import settings
from django.db import models


class PushDevice(models.Model):
    class Platforms(models.TextChoices):
        ANDROID = "android", "Android"
        IOS = "ios", "iOS"

    class Locales(models.TextChoices):
        SPANISH = "es", "Español"
        RUSSIAN = "ru", "Русский"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="push_devices",
    )
    registration_token = models.TextField(unique=True)
    platform = models.CharField(
        max_length=12,
        choices=Platforms.choices,
        default=Platforms.ANDROID,
    )
    locale = models.CharField(
        max_length=5,
        choices=Locales.choices,
        default=Locales.SPANISH,
    )
    is_active = models.BooleanField(default=True)
    notify_new_booking = models.BooleanField(default=True)
    notify_booking_cancelled = models.BooleanField(default=True)
    notify_booking_rescheduled = models.BooleanField(default=True)
    notify_employee_changed = models.BooleanField(default=True)
    notify_prepayment_received = models.BooleanField(default=True)
    notify_reminder_24h = models.BooleanField(default=True)
    notify_reminder_2h = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.user} · {self.platform}"

    @property
    def notification_preferences(self):
        return {
            "new_booking": self.notify_new_booking,
            "booking_cancelled": self.notify_booking_cancelled,
            "booking_rescheduled": self.notify_booking_rescheduled,
            "employee_changed": self.notify_employee_changed,
            "prepayment_received": self.notify_prepayment_received,
            "reminder_24h": self.notify_reminder_24h,
            "reminder_2h": self.notify_reminder_2h,
        }
