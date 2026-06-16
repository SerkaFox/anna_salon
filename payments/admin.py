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
        "stripe_checkout_session_id",
        "created_at",
        "paid_at",
    )
    list_filter = ("provider", "method", "status", "created_at", "paid_at")
    search_fields = (
        "order_number",
        "booking__client__first_name",
        "booking__client__last_name",
        "redsys_authorisation_code",
        "stripe_checkout_session_id",
        "stripe_payment_intent_id",
    )
    readonly_fields = ("created_at", "updated_at", "paid_at")
