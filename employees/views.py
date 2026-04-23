from collections import defaultdict
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render

from accounts.permissions import admin_required
from auditlog.services import log_event
from bookings.models import Booking

from .forms import EmployeeForm, WeeklyShiftFormSet
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


@login_required
@admin_required
def employee_list(request):
    query = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "revenue_desc").strip() or "revenue_desc"

    employees_qs = Employee.objects.select_related("user").prefetch_related("services").all()

    if query:
        employees_qs = employees_qs.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(phone__icontains=query) |
            Q(email__icontains=query) |
            Q(services__name__icontains=query)
        ).distinct()

    employees = list(employees_qs)
    employee_ids = [employee.pk for employee in employees]

    total_employee_earnings = Decimal("0.00")
    total_client_revenue = Decimal("0.00")
    total_salon_revenue = Decimal("0.00")
    total_done_bookings = 0
    total_unique_clients = set()

    stats_by_employee = {
        employee.pk: {
            "employee_earnings": Decimal("0.00"),
            "client_revenue": Decimal("0.00"),
            "salon_revenue": Decimal("0.00"),
            "bookings_count": 0,
            "clients": {},
            "services": defaultdict(int),
        }
        for employee in employees
    }

    if employee_ids:
        done_bookings = (
            Booking.objects
            .filter(employee_id__in=employee_ids, status=Booking.Statuses.DONE)
            .select_related("client", "service")
        )

        for booking in done_bookings:
            employee_stats = stats_by_employee[booking.employee_id]
            client_revenue = booking.client_price_snapshot or Decimal("0.00")
            employee_earnings = booking.employee_amount_snapshot or Decimal("0.00")
            salon_revenue = booking.salon_amount_snapshot or Decimal("0.00")

            employee_stats["employee_earnings"] += employee_earnings
            employee_stats["client_revenue"] += client_revenue
            employee_stats["salon_revenue"] += salon_revenue
            employee_stats["bookings_count"] += 1
            employee_stats["services"][booking.service.name] += 1

            client_stats = employee_stats["clients"].setdefault(
                booking.client_id,
                {
                    "id": booking.client_id,
                    "name": booking.client.full_name or str(booking.client),
                    "count": 0,
                    "spent": Decimal("0.00"),
                },
            )
            client_stats["count"] += 1
            client_stats["spent"] += client_revenue

            total_employee_earnings += employee_earnings
            total_client_revenue += client_revenue
            total_salon_revenue += salon_revenue
            total_done_bookings += 1
            total_unique_clients.add(booking.client_id)

    for employee in employees:
        stats = stats_by_employee[employee.pk]
        clients = sorted(
            stats["clients"].values(),
            key=lambda item: (-item["count"], -item["spent"], item["name"]),
        )
        services = sorted(
            (
                {"name": service_name, "count": count}
                for service_name, count in stats["services"].items()
            ),
            key=lambda item: (-item["count"], item["name"]),
        )

        employee.employee_earnings = stats["employee_earnings"]
        employee.client_revenue = stats["client_revenue"]
        employee.salon_revenue = stats["salon_revenue"]
        employee.bookings_count = stats["bookings_count"]
        employee.clients_count = len(clients)
        employee.repeat_clients_count = sum(1 for item in clients if item["count"] > 1)
        employee.top_clients = clients[:3]
        employee.top_services = services[:3]
        employee.avg_ticket = (
            stats["client_revenue"] / stats["bookings_count"]
            if stats["bookings_count"]
            else Decimal("0.00")
        )

    revenue_ranking = sorted(
        employees,
        key=lambda employee: (-employee.employee_earnings, -employee.client_revenue, employee.full_name),
    )
    clients_ranking = sorted(
        employees,
        key=lambda employee: (-employee.clients_count, -employee.bookings_count, employee.full_name),
    )

    for rank, employee in enumerate(revenue_ranking, start=1):
        employee.revenue_rank = rank

    for rank, employee in enumerate(clients_ranking, start=1):
        employee.clients_rank = rank

    sort_options = {
        "revenue_desc": {
            "label": "Por ingresos",
            "key": lambda employee: (-employee.employee_earnings, -employee.client_revenue, employee.full_name),
        },
        "clients_desc": {
            "label": "Por clientes",
            "key": lambda employee: (-employee.clients_count, -employee.bookings_count, employee.full_name),
        },
        "bookings_desc": {
            "label": "Por visitas",
            "key": lambda employee: (-employee.bookings_count, -employee.employee_earnings, employee.full_name),
        },
        "avg_ticket_desc": {
            "label": "Por ticket medio",
            "key": lambda employee: (-employee.avg_ticket, -employee.bookings_count, employee.full_name),
        },
        "name_asc": {
            "label": "Por nombre",
            "key": lambda employee: employee.full_name,
        },
    }
    if sort not in sort_options:
        sort = "revenue_desc"

    employees.sort(key=sort_options[sort]["key"])

    context = {
        "active_section": "employees",
        "employees": employees,
        "query": query,
        "sort": sort,
        "sort_options": sort_options,
        "employees_count": len(employees),
        "stats": [
            {"label": "Empleados en lista", "value": len(employees)},
            {"label": "Visitas completadas", "value": total_done_bookings},
            {"label": "Clientes únicos", "value": len(total_unique_clients)},
        ],
        "money_stats": [
            {"label": "Ganado por empleados", "value": total_employee_earnings},
            {"label": "Facturado a clientes", "value": total_client_revenue},
            {"label": "Ingresos del salón", "value": total_salon_revenue},
        ],
    }
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return render(request, "employees/_employee_analytics_content.html", context)
    return render(request, "employees/employee_list.html", context)


@login_required
@admin_required
def employee_create(request):
    employee = Employee()

    if request.method == "POST":
        form = EmployeeForm(request.POST, instance=employee)
        weekly_formset = build_weekly_shift_formset(request=request, instance=employee)
        if form.is_valid() and weekly_formset.is_valid():
            with transaction.atomic():
                employee = form.save()
                weekly_formset.instance = employee
                weekly_formset.save()
            log_event(
                actor=request.user,
                section="employee",
                action="create",
                instance=employee,
                message=f"Empleado creado: {employee.full_name}.",
            )
            messages.success(request, f"Empleado creado: {employee.full_name}")
            return redirect("employees:update", pk=employee.pk)
    else:
        form = EmployeeForm(instance=employee)
        weekly_formset = build_weekly_shift_formset(instance=employee)

    context = {
        "active_section": "employees",
        "form": form,
        "weekly_formset": weekly_formset,
        "is_edit": False,
    }
    return render(request, "employees/employee_form.html", context)


@login_required
@admin_required
def employee_update(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    ensure_weekly_shifts(employee)

    if request.method == "POST":
        form = EmployeeForm(request.POST, instance=employee)
        weekly_formset = build_weekly_shift_formset(request=request, instance=employee)
        if form.is_valid() and weekly_formset.is_valid():
            with transaction.atomic():
                employee = form.save()
                weekly_formset.save()
            log_event(
                actor=request.user,
                section="employee",
                action="update",
                instance=employee,
                message=f"Empleado actualizado: {employee.full_name}.",
            )
            messages.success(request, f"Empleado actualizado: {employee.full_name}")
            return redirect("employees:list")
    else:
        form = EmployeeForm(instance=employee)
        weekly_formset = build_weekly_shift_formset(instance=employee)

    context = {
        "active_section": "employees",
        "form": form,
        "weekly_formset": weekly_formset,
        "employee": employee,
        "is_edit": True,
    }
    return render(request, "employees/employee_form.html", context)


@login_required
@admin_required
def employee_delete(request, pk):
    employee = get_object_or_404(Employee, pk=pk)

    if request.method == "POST":
        employee_name = employee.full_name
        try:
            employee.delete()
            log_event(
                actor=request.user,
                section="employee",
                action="delete",
                message=f"Empleado eliminado: {employee_name}.",
            )
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
