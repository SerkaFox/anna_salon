from django.contrib import admin

from .models import Employee, EmployeeScheduleOverride, EmployeeWeeklyShift


class EmployeeWeeklyShiftInline(admin.TabularInline):
    model = EmployeeWeeklyShift
    extra = 0


class EmployeeScheduleOverrideInline(admin.TabularInline):
    model = EmployeeScheduleOverride
    extra = 0


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "email", "commission_percent", "is_active")
    list_filter = ("is_active",)
    search_fields = ("first_name", "last_name", "phone", "email")
    inlines = [EmployeeWeeklyShiftInline, EmployeeScheduleOverrideInline]
