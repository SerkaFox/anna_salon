from django.contrib import admin

from .models import ClientReview


@admin.register(ClientReview)
class ClientReviewAdmin(admin.ModelAdmin):
    list_display = ("client", "booking", "rating", "created_at", "updated_at")
    list_filter = ("rating", "created_at")
    search_fields = (
        "client__first_name",
        "client__last_name",
        "booking__service__name",
        "text",
    )
