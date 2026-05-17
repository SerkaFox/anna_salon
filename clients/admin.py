from django.contrib import admin

from .models import Client, ClientRewardRedemption, ClientRewardRule


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "email", "is_active", "created_at")
    search_fields = ("first_name", "last_name", "phone", "email")
    list_filter = ("is_active", "created_at")


@admin.register(ClientRewardRule)
class ClientRewardRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "reward_type", "threshold", "discount_percent", "is_active", "sort_order")
    list_filter = ("reward_type", "is_active")
    ordering = ("sort_order", "name")


@admin.register(ClientRewardRedemption)
class ClientRewardRedemptionAdmin(admin.ModelAdmin):
    list_display = ("client", "reward_rule", "booking", "discount_amount", "created_at")
    list_filter = ("reward_rule", "created_at")
    search_fields = ("client__first_name", "client__last_name")
