from django.contrib import admin

from .models import InstagramPost


@admin.register(InstagramPost)
class InstagramPostAdmin(admin.ModelAdmin):
    list_display = ("title", "instagram_media_id", "media_type", "synced_from_api", "active", "featured", "sort_order", "created_at")
    list_filter = ("active", "featured", "synced_from_api", "media_type", "created_at")
    search_fields = ("title", "instagram_media_id", "instagram_url", "caption")
    readonly_fields = ("created_at", "updated_at")
