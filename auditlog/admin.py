from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "section", "action", "actor", "target_repr", "message")
    list_filter = ("section", "action", "created_at")
    search_fields = ("message", "target_repr", "actor__username", "actor__first_name", "actor__last_name")
