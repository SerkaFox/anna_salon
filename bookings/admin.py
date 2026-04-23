from django.contrib import admin

from .models import Booking, BookingPhoto


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("start_at", "client", "service", "employee", "status", "source")
    list_filter = ("status", "source", "employee", "service")
    search_fields = (
        "client__first_name",
        "client__last_name",
        "employee__first_name",
        "employee__last_name",
        "service__name",
    )
    date_hierarchy = "start_at"


@admin.register(BookingPhoto)
class BookingPhotoAdmin(admin.ModelAdmin):
    list_display = ("booking", "client", "photo_type", "is_key_reference", "created_at")
    list_filter = ("photo_type", "is_key_reference", "created_at")
    search_fields = ("client__first_name", "client__last_name", "booking__service__name")
