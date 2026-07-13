import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class WhatsAppConnection(models.Model):
    class Statuses(models.TextChoices):
        DISCONNECTED = "disconnected", "Disconnected"
        QR_PENDING = "qr_pending", "QR pending"
        CONNECTED = "connected", "Connected"
        ERROR = "error", "Error"

    name = models.CharField(max_length=120, unique=True, default="main")
    status = models.CharField(max_length=20, choices=Statuses.choices, default=Statuses.DISCONNECTED)
    phone = models.CharField(max_length=40, blank=True)
    bridge_session_id = models.CharField(max_length=120, blank=True)
    last_error = models.TextField(blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.status})"


class WhatsAppLoginLink(models.Model):
    token = models.CharField(max_length=80, unique=True, db_index=True)
    connection = models.ForeignKey(WhatsAppConnection, on_delete=models.CASCADE, related_name="login_links")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=180, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @classmethod
    def create_for_connection(cls, connection, *, ttl_minutes=15, note=""):
        return cls.objects.create(
            token=secrets.token_urlsafe(32),
            connection=connection,
            expires_at=timezone.now() + timedelta(minutes=ttl_minutes),
            note=note,
        )

    @property
    def is_valid(self):
        return self.used_at is None and self.expires_at > timezone.now()

    def mark_used(self):
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])

    def __str__(self):
        return f"{self.connection.name} login link"


class WhatsAppMessage(models.Model):
    class Kinds(models.TextChoices):
        BOOKING_CONFIRMATION = "booking_confirmation", "Booking confirmation"
        BOOKING_CANCELLED = "booking_cancelled", "Booking cancelled"
        BOOKING_RESCHEDULED = "booking_rescheduled", "Booking rescheduled"
        REMINDER_24H = "reminder_24h", "Reminder 24h"
        REMINDER_2H = "reminder_2h", "Reminder 2h"
        MANUAL = "manual", "Manual"

    class Statuses(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    connection = models.ForeignKey(WhatsAppConnection, on_delete=models.PROTECT, related_name="messages")
    booking = models.ForeignKey("bookings.Booking", on_delete=models.CASCADE, null=True, blank=True, related_name="whatsapp_messages")
    client = models.ForeignKey("clients.Client", on_delete=models.SET_NULL, null=True, blank=True, related_name="whatsapp_messages")
    kind = models.CharField(max_length=40, choices=Kinds.choices)
    to_phone = models.CharField(max_length=40)
    body = models.TextField()
    status = models.CharField(max_length=20, choices=Statuses.choices, default=Statuses.QUEUED, db_index=True)
    scheduled_for = models.DateTimeField(default=timezone.now, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    provider_message_id = models.CharField(max_length=180, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["booking", "kind"], name="unique_whatsapp_booking_message_kind"),
        ]

    def __str__(self):
        return f"{self.kind} to {self.to_phone} ({self.status})"
