from django.contrib import admin

from .models import Booking, BookingPhoto, BookingPrepayment, BookingWaitlistEntry


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
    list_display = ("booking", "client", "photo_type", "is_key_reference", "is_visible_to_client", "created_at")
    list_filter = ("photo_type", "is_key_reference", "is_visible_to_client", "created_at")
    search_fields = ("client__first_name", "client__last_name", "booking__service__name")


@admin.register(BookingPrepayment)
class BookingPrepaymentAdmin(admin.ModelAdmin):
    list_display = ("booking", "amount", "status", "refundable_until", "refunded_at", "forfeited_at")
    list_filter = ("status", "refundable_until", "created_at")
    search_fields = ("booking__client__first_name", "booking__client__last_name", "booking__service__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(BookingWaitlistEntry)
class BookingWaitlistEntryAdmin(admin.ModelAdmin):
    list_display = ("desired_date", "name", "service", "employee", "time_range", "status", "notified_at")
    list_filter = ("status", "desired_date", "employee", "service")
    search_fields = ("name", "phone", "email", "service__name", "employee__first_name", "employee__last_name")
    readonly_fields = ("created_at", "updated_at", "notified_at")
