from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "order_number",
        "booking",
        "amount",
        "currency",
        "provider",
        "method",
        "status",
        "redsys_response_code",
        "created_at",
        "paid_at",
    )
    list_filter = ("provider", "method", "status", "created_at", "paid_at")
    search_fields = (
        "order_number",
        "booking__client__first_name",
        "booking__client__last_name",
        "redsys_authorisation_code",
    )
    readonly_fields = ("created_at", "updated_at", "paid_at")
