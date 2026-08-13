from django.contrib import admin

from .models import Payment, PaymentRefund, StripePayoutRequest


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
        "amount_refunded",
        "redsys_response_code",
        "stripe_checkout_session_id",
        "stripe_refund_id",
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
        "stripe_refund_id",
    )
    readonly_fields = ("created_at", "updated_at", "paid_at")


@admin.register(PaymentRefund)
class PaymentRefundAdmin(admin.ModelAdmin):
    list_display = ("payment", "amount", "status", "stripe_refund_id", "created_at", "refunded_at")
    list_filter = ("status", "created_at", "refunded_at")
    search_fields = (
        "payment__order_number",
        "payment__booking__client__first_name",
        "payment__booking__client__last_name",
        "stripe_refund_id",
    )
    readonly_fields = ("created_at", "refunded_at")


@admin.register(StripePayoutRequest)
class StripePayoutRequestAdmin(admin.ModelAdmin):
    list_display = ("created_at", "requested_by", "amount", "currency", "method", "status", "stripe_payout_id")
    list_filter = ("method", "status", "created_at")
    search_fields = ("stripe_payout_id", "requested_by__username")
    readonly_fields = (
        "idempotency_key", "requested_by", "amount", "currency", "method",
        "destination", "stripe_payout_id", "status", "arrival_date", "error",
        "created_at", "updated_at",
    )
