from django.contrib import admin

from .models import WhatsAppConnection, WhatsAppLoginLink, WhatsAppMessage


@admin.register(WhatsAppConnection)
class WhatsAppConnectionAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "phone", "bridge_session_id", "last_seen_at", "updated_at")
    search_fields = ("name", "phone", "bridge_session_id")


@admin.register(WhatsAppLoginLink)
class WhatsAppLoginLinkAdmin(admin.ModelAdmin):
    list_display = ("connection", "created_at", "expires_at", "used_at", "note")
    search_fields = ("connection__name", "note")
    readonly_fields = ("token", "created_at")


@admin.register(WhatsAppMessage)
class WhatsAppMessageAdmin(admin.ModelAdmin):
    list_display = ("kind", "to_phone", "status", "booking", "scheduled_for", "sent_at", "created_at")
    list_filter = ("kind", "status", "connection")
    search_fields = ("to_phone", "body", "provider_message_id", "booking__client__first_name", "booking__client__last_name")
    readonly_fields = ("created_at", "updated_at", "sent_at", "provider_message_id", "error")
