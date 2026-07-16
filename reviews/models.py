from django.db import models


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
