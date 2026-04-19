from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render

from .forms import EmployeeForm, ScheduleOverrideFormSet, WeeklyShiftFormSet
from .models import Employee, EmployeeWeeklyShift, Weekday


def ensure_weekly_shifts(employee):
    for weekday, _label in Weekday.choices:
        EmployeeWeeklyShift.objects.get_or_create(
            employee=employee,
            weekday=weekday,
            defaults={"is_day_off": weekday == Weekday.SUNDAY},
        )


def build_weekly_shift_formset(request=None, instance=None):
    initial = [{"weekday": weekday} for weekday, _label in Weekday.choices]
    kwargs = {"instance": instance, "prefix": "weekly"}
    if request is not None:
        kwargs["data"] = request.POST
    elif not getattr(instance, "pk", None):
        kwargs["initial"] = initial
    return WeeklyShiftFormSet(**kwargs)


def build_override_formset(request=None, instance=None):
    kwargs = {"instance": instance, "prefix": "overrides"}
    if request is not None:
        kwargs["data"] = request.POST
    return ScheduleOverrideFormSet(**kwargs)


@login_required
def employee_list(request):
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()

    employees = Employee.objects.select_related("user").prefetch_related("services").all()

    if query:
        employees = employees.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(phone__icontains=query) |
            Q(email__icontains=query) |
            Q(services__name__icontains=query)
        ).distinct()

    if status == "active":
        employees = employees.filter(is_active=True)
    elif status == "inactive":
        employees = employees.filter(is_active=False)

    context = {
        "active_section": "employees",
        "employees": employees,
        "query": query,
        "status": status,
        "employees_count": employees.count(),
    }
    return render(request, "employees/employee_list.html", context)


@login_required
def employee_create(request):
    employee = Employee()

    if request.method == "POST":
        form = EmployeeForm(request.POST, instance=employee)
        weekly_formset = build_weekly_shift_formset(request=request, instance=employee)
        override_formset = build_override_formset(request=request, instance=employee)
        if form.is_valid() and weekly_formset.is_valid() and override_formset.is_valid():
            with transaction.atomic():
                employee = form.save()
                weekly_formset.instance = employee
                weekly_formset.save()
                override_formset.instance = employee
                override_formset.save()
            messages.success(request, f"Empleado creado: {employee.full_name}")
            return redirect("employees:update", pk=employee.pk)
    else:
        form = EmployeeForm(instance=employee)
        weekly_formset = build_weekly_shift_formset(instance=employee)
        override_formset = build_override_formset(instance=employee)

    context = {
        "active_section": "employees",
        "form": form,
        "weekly_formset": weekly_formset,
        "override_formset": override_formset,
        "is_edit": False,
    }
    return render(request, "employees/employee_form.html", context)


@login_required
def employee_update(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    ensure_weekly_shifts(employee)

    if request.method == "POST":
        form = EmployeeForm(request.POST, instance=employee)
        weekly_formset = build_weekly_shift_formset(request=request, instance=employee)
        override_formset = build_override_formset(request=request, instance=employee)
        if form.is_valid() and weekly_formset.is_valid() and override_formset.is_valid():
            with transaction.atomic():
                employee = form.save()
                weekly_formset.save()
                override_formset.save()
            messages.success(request, f"Empleado actualizado: {employee.full_name}")
            return redirect("employees:list")
    else:
        form = EmployeeForm(instance=employee)
        weekly_formset = build_weekly_shift_formset(instance=employee)
        override_formset = build_override_formset(instance=employee)

    context = {
        "active_section": "employees",
        "form": form,
        "weekly_formset": weekly_formset,
        "override_formset": override_formset,
        "employee": employee,
        "is_edit": True,
    }
    return render(request, "employees/employee_form.html", context)


@login_required
def employee_delete(request, pk):
    employee = get_object_or_404(Employee, pk=pk)

    if request.method == "POST":
        employee_name = employee.full_name
        try:
            employee.delete()
            messages.success(request, f"Empleado eliminado: {employee_name}")
        except ProtectedError:
            messages.error(
                request,
                "No se puede eliminar este empleado porque tiene reservas u otros datos relacionados."
            )
        return redirect("employees:list")

    return render(
        request,
        "employees/employee_confirm_delete.html",
        {
            "active_section": "employees",
            "employee": employee,
        },
    )
