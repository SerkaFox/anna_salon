import json
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from clients.models import Client
from employees.models import Employee
from salon.models import Zone
from services_app.models import Service

from .forms import BookingForm
from .models import Booking
from .utils import (
    build_calendar_hour_lines,
    booking_layout_data,
    fits_employee_schedule,
    get_bookings_for_day,
    find_available_slots_nearby,
)

@login_required
def booking_list(request):
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()

    bookings = Booking.objects.select_related(
        "client", "employee", "service", "zone"
    ).all()

    if query:
        bookings = bookings.filter(
            Q(client__first_name__icontains=query) |
            Q(client__last_name__icontains=query) |
            Q(employee__first_name__icontains=query) |
            Q(employee__last_name__icontains=query) |
            Q(service__name__icontains=query)
        )

    if status:
        bookings = bookings.filter(status=status)

    context = {
        "active_section": "bookings",
        "bookings": bookings,
        "query": query,
        "status": status,
        "bookings_count": bookings.count(),
        "status_choices": Booking.Statuses.choices,
    }
    return render(request, "bookings/booking_list.html", context)


@login_required
def client_reward_api(request):
    client_id = request.GET.get("client_id")

    if not client_id:
        return JsonResponse({
            "available_rewards": 0,
            "can_apply": False,
            "discount_percent": "20",
            "message": "Selecciona un cliente.",
        })

    try:
        client = Client.objects.get(pk=client_id)
    except Client.DoesNotExist:
        return JsonResponse({
            "available_rewards": 0,
            "can_apply": False,
            "discount_percent": "20",
            "message": "Cliente no encontrado.",
        })

    successful_count = Client.objects.filter(
        referred_by=client,
        bookings__status=Booking.Statuses.DONE,
    ).distinct().count()

    available_rewards = max((successful_count // 5) - client.referral_rewards_used, 0)

    return JsonResponse({
        "available_rewards": available_rewards,
        "can_apply": available_rewards > 0,
        "discount_percent": "20",
        "message": (
            f"Premios disponibles: {available_rewards}. Descuento 20%."
            if available_rewards > 0
            else "Este cliente no tiene premios disponibles."
        ),
    })

@login_required
def booking_create(request):
    from_booking_id = request.GET.get("from_booking")

    initial = {}

    if from_booking_id and request.method == "GET":
        base_booking = get_object_or_404(
            Booking.objects.select_related("client", "employee", "service", "zone"),
            pk=from_booking_id,
        )

        initial = {
            "client": base_booking.client,
            "start_at": timezone.localtime(base_booking.end_at).strftime("%Y-%m-%dT%H:%M"),
            "end_at": timezone.localtime(base_booking.end_at).strftime("%Y-%m-%dT%H:%M"),
            "status": Booking.Statuses.CONFIRMED,
            "notes": "",
        }

    if request.method == "POST":
        form = BookingForm(request.POST)
        if form.is_valid():
            booking = form.save()
            messages.success(request, f"Reserva creada: {booking}")
            return redirect("bookings:list")
    else:
        form = BookingForm(initial=initial)

    return render(
        request,
        "bookings/booking_form.html",
        {
            "active_section": "bookings",
            "form": form,
            "is_edit": False,
            "prefill_from_booking_id": from_booking_id or "",
        },
    )


@login_required
def booking_update(request, pk):
    booking = get_object_or_404(Booking, pk=pk)

    if request.method == "POST":
        form = BookingForm(request.POST, instance=booking)
        if form.is_valid():
            booking = form.save()
            messages.success(request, f"Reserva actualizada: {booking}")
            return redirect("bookings:list")
    else:
        form = BookingForm(
            instance=booking,
            initial={
                "start_at": timezone.localtime(booking.start_at).strftime("%Y-%m-%dT%H:%M"),
                "end_at": timezone.localtime(booking.end_at).strftime("%Y-%m-%dT%H:%M"),
            }
        )

    return render(
        request,
        "bookings/booking_form.html",
        {
            "active_section": "bookings",
            "form": form,
            "booking": booking,
            "is_edit": True,
        },
    )

@login_required
def booking_delete(request, pk):
    booking = get_object_or_404(Booking, pk=pk)

    if request.method == "POST":
        booking_label = str(booking)
        booking.delete()
        messages.success(request, f"Reserva eliminada: {booking_label}")
        return redirect("bookings:list")

    return render(
        request,
        "bookings/booking_confirm_delete.html",
        {
            "active_section": "bookings",
            "booking": booking,
        },
    )

@login_required
def service_data_api(request):
    service_id = request.GET.get("service_id")

    if not service_id:
        return JsonResponse({
            "requires_zone": False,
            "zones": [],
            "employees": [],
            "duration_minutes": None,
        })

    try:
        service = Service.objects.prefetch_related("allowed_zones", "employees").get(pk=service_id, is_active=True)
    except Service.DoesNotExist:
        return JsonResponse({
            "requires_zone": False,
            "zones": [],
            "employees": [],
            "duration_minutes": None,
        })

    if service.requires_zone:
        zones = list(
            service.allowed_zones.filter(is_active=True)
            .order_by("name")
            .values("id", "name")
        )
    else:
        zones = []

    employees = list(
        service.employees.filter(is_active=True)
        .order_by("first_name", "last_name")
        .values("id", "first_name", "last_name")
    )

    return JsonResponse({
        "requires_zone": service.requires_zone,
        "zones": zones,
        "employees": employees,
        "duration_minutes": service.duration_minutes,
    })
    
@login_required
def booking_availability(request):
    service_id = request.GET.get("service")
    employee_id = request.GET.get("employee")
    zone_id = request.GET.get("zone")
    date_str = request.GET.get("date")

    selected_date = timezone.localdate()
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            pass

    service = None
    employee = None
    zone = None
    availability = []

    if service_id and employee_id:
        try:
            service = Service.objects.get(pk=service_id, is_active=True)
            employee = Employee.objects.get(pk=employee_id, is_active=True)
        except (Service.DoesNotExist, Employee.DoesNotExist):
            service = None
            employee = None

        if service and employee:
            if zone_id:
                try:
                    zone = Zone.objects.get(pk=zone_id, is_active=True)
                except Zone.DoesNotExist:
                    zone = None

            if employee.services.filter(pk=service.pk).exists():
                availability = find_available_slots_nearby(
                    start_date=selected_date,
                    employee=employee,
                    service=service,
                    zone=zone,
                )

    context = {
        "active_section": "calendar",
        "selected_date": selected_date,
        "service_id": service_id or "",
        "employee_id": employee_id or "",
        "zone_id": zone_id or "",
        "service": service,
        "employee": employee,
        "zone": zone,
        "availability": availability,
        "services": Service.objects.filter(is_active=True).order_by("name"),
        "employees": Employee.objects.filter(is_active=True).order_by("first_name", "last_name"),
        "zones": Zone.objects.filter(is_active=True).order_by("name"),
    }
    return render(request, "bookings/availability.html", context)



@login_required
def booking_calendar_day(request):
    date_str = request.GET.get("date")
    today = timezone.localdate()

    try:
        current_date = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else today
    except ValueError:
        current_date = today

    visible_days = [current_date - timedelta(days=1), current_date, current_date + timedelta(days=1)]
    bookings_by_day = {day: list(get_bookings_for_day(day)) for day in visible_days}
    all_bookings = [booking for day in visible_days for booking in bookings_by_day[day]]
    employees_by_id = {}
    employee_agenda_map = {}
    total_bookings = 0

    day_columns = []
    for day in visible_days:
        day_cards = []
        for booking in bookings_by_day[day]:
            card = booking_layout_data(booking)
            card["target_id"] = f"booking-card-{booking.pk}"
            day_cards.append(card)
            total_bookings += 1

            employees_by_id[booking.employee_id] = booking.employee
            employee_agenda_map.setdefault(
                booking.employee_id,
                {
                    "employee": booking.employee,
                    "bookings": [],
                },
            )["bookings"].append({
                "target_id": card["target_id"],
                "date": day,
                "time_label": f"{card['start_at'].strftime('%H:%M')} - {card['end_at'].strftime('%H:%M')}",
                "client": card["client"],
                "service": card["service"],
            })

        zone_map = {}
        for booking in bookings_by_day[day]:
            if not booking.zone_id:
                continue
            zone_map.setdefault(
                booking.zone_id,
                {
                    "name": booking.zone.name,
                    "color": booking.zone.color,
                    "count": 0,
                },
            )["count"] += 1

        day_columns.append({
            "date": day,
            "is_today": day == today,
            "is_selected": day == current_date,
            "cards": day_cards,
            "zones": sorted(zone_map.values(), key=lambda item: item["name"]),
        })

    employee_agenda = sorted(
        employee_agenda_map.values(),
        key=lambda item: (item["employee"].first_name, item["employee"].last_name),
    )

    context = {
        "active_section": "calendar",
        "current_date": current_date,
        "hour_lines": build_calendar_hour_lines(),
        "calendar_height": (20 - 9) * 60,
        "prev_date": current_date - timedelta(days=1),
        "next_date": current_date + timedelta(days=1),
        "today": today,
        "day_columns": day_columns,
        "employee_agenda": employee_agenda,
        "total_bookings": total_bookings,
    }
    return render(request, "bookings/calendar_day.html", context)
    
@login_required
def booking_slot_check_api(request):
    service_id = request.GET.get("service")
    employee_id = request.GET.get("employee")
    zone_id = request.GET.get("zone")
    start_at_str = request.GET.get("start_at")
    exclude_booking_id = request.GET.get("exclude_booking_id")

    if not service_id or not employee_id or not start_at_str:
        return JsonResponse({
            "ok": False,
            "message": "Faltan datos para comprobar disponibilidad.",
        })

    try:
        service = Service.objects.get(pk=service_id, is_active=True)
        employee = Employee.objects.get(pk=employee_id, is_active=True)
    except (Service.DoesNotExist, Employee.DoesNotExist):
        return JsonResponse({
            "ok": False,
            "message": "Servicio o empleado no válido.",
        })

    zone = None
    if zone_id:
        try:
            zone = Zone.objects.get(pk=zone_id, is_active=True)
        except Zone.DoesNotExist:
            return JsonResponse({
                "ok": False,
                "message": "Zona no válida.",
            })

    try:
        start_at = datetime.strptime(start_at_str, "%Y-%m-%dT%H:%M")
        if timezone.is_naive(start_at):
            start_at = timezone.make_aware(start_at)
    except ValueError:
        return JsonResponse({
            "ok": False,
            "message": "Fecha/hora inválida.",
        })

    end_at = start_at + timedelta(minutes=service.duration_minutes)

    fits_schedule, schedule_message = fits_employee_schedule(employee, start_at, end_at)
    if not fits_schedule:
        return JsonResponse({
            "ok": False,
            "message": schedule_message,
        })

    if service.requires_zone:
        if not zone:
            return JsonResponse({
                "ok": False,
                "message": "Este servicio requiere una zona.",
            })
        if not service.allowed_zones.filter(pk=zone.pk).exists():
            return JsonResponse({
                "ok": False,
                "message": "La zona no está permitida para este servicio.",
            })

    if not employee.services.filter(pk=service.pk).exists():
        return JsonResponse({
            "ok": False,
            "message": "Este empleado no realiza el servicio seleccionado.",
        })

    exclude_id = None
    if exclude_booking_id:
        try:
            exclude_id = int(exclude_booking_id)
        except ValueError:
            exclude_id = None

    available = is_slot_available(
        employee=employee,
        service=service,
        zone=zone,
        start_at=start_at,
        end_at=end_at,
        exclude_booking_id=exclude_id,
    )

    if not available:
        return JsonResponse({
            "ok": False,
            "message": "Ese horario no está disponible para el empleado o la zona.",
            "end_at": timezone.localtime(end_at).strftime("%Y-%m-%dT%H:%M"),
        })

    return JsonResponse({
        "ok": True,
        "message": "Horario disponible.",
        "end_at": timezone.localtime(end_at).strftime("%Y-%m-%dT%H:%M"),
    })


@login_required
@require_POST
def booking_reschedule_api(request, pk):
    booking = get_object_or_404(Booking, pk=pk)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "message": "Payload inválido."}, status=400)

    employee_id = payload.get("employee_id")
    start_at_str = payload.get("start_at")

    if not employee_id or not start_at_str:
        return JsonResponse({"ok": False, "message": "Faltan datos para mover la reserva."}, status=400)

    employee = get_object_or_404(Employee, pk=employee_id, is_active=True)

    try:
        start_at = datetime.strptime(start_at_str, "%Y-%m-%dT%H:%M")
        if timezone.is_naive(start_at):
            start_at = timezone.make_aware(start_at)
    except ValueError:
        return JsonResponse({"ok": False, "message": "Fecha/hora inválida."}, status=400)

    end_at = start_at + timedelta(minutes=booking.duration_snapshot or booking.service.duration_minutes)

    form = BookingForm(
        data={
            "client": booking.client_id,
            "employee": employee.pk,
            "service": booking.service_id,
            "zone": booking.zone_id or "",
            "start_at": timezone.localtime(start_at).strftime("%Y-%m-%dT%H:%M"),
            "end_at": timezone.localtime(end_at).strftime("%Y-%m-%dT%H:%M"),
            "status": booking.status,
            "notes": booking.notes,
        },
        instance=booking,
    )

    if not form.is_valid():
        errors = form.non_field_errors()
        message = errors[0] if errors else "No se pudo mover la reserva."
        if not errors:
            for field_errors in form.errors.values():
                if field_errors:
                    message = field_errors[0]
                    break
        return JsonResponse({"ok": False, "message": message}, status=400)

    moved_booking = form.save()
    card = booking_layout_data(moved_booking)

    return JsonResponse({
        "ok": True,
        "message": "Reserva movida correctamente.",
        "booking": {
            "id": moved_booking.pk,
            "employee_id": moved_booking.employee_id,
            "start_at": card["start_at"].strftime("%Y-%m-%dT%H:%M"),
            "end_at": card["end_at"].strftime("%Y-%m-%dT%H:%M"),
        },
    })
