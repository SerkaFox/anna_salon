from django.contrib import admin

from .models import FiscalDocument, Payment


@admin.register(FiscalDocument)
class FiscalDocumentAdmin(admin.ModelAdmin):
    list_display = (
        "number",
        "document_type",
        "status",
        "issue_date",
        "client_name",
        "service_name",
        "total_amount",
    )
    list_filter = ("document_type", "status", "issue_date")
    search_fields = (
        "number",
        "booking__client__first_name",
        "booking__client__last_name",
        "booking__service__name",
    )
    readonly_fields = ("created_at", "updated_at")

    def client_name(self, obj):
        return obj.booking.client

    client_name.short_description = "Cliente"

    def service_name(self, obj):
        return obj.booking.service

    service_name.short_description = "Servicio"


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("paid_at", "method", "amount", "client_name", "document_number")
    list_filter = ("method", "paid_at")
    search_fields = (
        "fiscal_document__number",
        "booking__client__first_name",
        "booking__client__last_name",
        "reference",
    )

    def client_name(self, obj):
        return obj.booking.client

    client_name.short_description = "Cliente"

    def document_number(self, obj):
        return obj.fiscal_document.number

    document_number.short_description = "Documento"
