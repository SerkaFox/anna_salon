from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class GoogleReview(models.Model):
    google_review_id = models.CharField(max_length=300, unique=True)
    reviewer_name = models.CharField(max_length=120)
    reviewer_url = models.URLField(blank=True)
    reviewer_photo = models.URLField(blank=True)
    rating = models.PositiveSmallIntegerField()
    text = models.TextField(blank=True)
    published_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-published_at"]

    def __str__(self):
        return f"{self.reviewer_name} — ⭐{self.rating} (Google)"

    @property
    def stars_range(self):
        return range(self.rating)

    @property
    def empty_stars_range(self):
        return range(5 - self.rating)

    @classmethod
    def review_url(cls):
        place_id = getattr(settings, "GOOGLE_PLACE_ID", "")
        return f"https://search.google.com/local/writereview?placeid={place_id}"


class GoogleOAuthToken(models.Model):
    """Stores OAuth tokens for Google Business Profile API."""
    access_token = models.TextField(blank=True)
    refresh_token = models.TextField()
    token_expiry = models.DateTimeField(null=True, blank=True)
    account_name = models.CharField(max_length=200, blank=True)   # accounts/XXXXXXX
    location_name = models.CharField(max_length=200, blank=True)  # accounts/X/locations/Y
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Google OAuth Token"

    def __str__(self):
        return f"Google OAuth ({self.account_name or 'not configured'})"

    @classmethod
    def get(cls):
        return cls.objects.first()


class TreatwellReview(models.Model):
    treatwell_id = models.BigIntegerField(unique=True, null=True, blank=True)
    reviewer_name = models.CharField(max_length=120)
    date = models.DateField()
    rating = models.PositiveSmallIntegerField()
    text = models.TextField(blank=True)
    services = models.JSONField(default=list, blank=True)
    employee = models.CharField(max_length=200, blank=True)
    verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(fields=["-date"]),
            models.Index(fields=["rating"]),
        ]

    def __str__(self):
        return f"{self.reviewer_name} — ⭐{self.rating} ({self.date})"

    @property
    def stars_range(self):
        return range(self.rating)

    @property
    def empty_stars_range(self):
        return range(5 - self.rating)

    @property
    def primary_service(self):
        return self.services[0] if self.services else ""


class ClientReview(models.Model):
    booking = models.OneToOneField(
        "bookings.Booking",
        on_delete=models.CASCADE,
        related_name="client_review",
    )
    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.CASCADE,
        related_name="reviews",
    )
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    text = models.TextField(blank=True, max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if self.booking_id:
            self.client_id = self.booking.client_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.client} - {self.rating}/5 - {self.booking}"
