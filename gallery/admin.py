from django.contrib import admin

from .models import InstagramPost


@admin.register(InstagramPost)
class InstagramPostAdmin(admin.ModelAdmin):
    list_display = ("title", "instagram_url", "active", "featured", "sort_order", "created_at")
    list_filter = ("active", "featured", "created_at")
    search_fields = ("title", "instagram_url", "caption")
    readonly_fields = ("created_at", "updated_at")
