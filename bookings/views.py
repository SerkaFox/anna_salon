import json
import mimetypes
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import FileResponse
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from clients.models import Client
from employees.models import Employee
from employees.models import EmployeeScheduleOverride
from salon.models import Zone
from services_app.models import Service

from .forms import BookingForm, BookingPhotoForm
from .models import Booking, BookingPhoto
from .utils import (
    DEFAULT_WORK_END_HOUR,
    DEFAULT_WORK_START_HOUR,
    build_calendar_hour_lines,
    build_time_block_layout_data,
    booking_layout_data,
    fits_employee_schedule,
    get_bookings_for_day,
    get_employee_schedule,
    get_employee_time_blocks,
    find_available_slots_nearby,
    is_slot_available,
    minutes_from_work_start,
)


def _build_booking_photo_context(booking):
    photos = list(booking.photos.all())
    latest_before = next((photo for photo in photos if photo.photo_type == BookingPhoto.PhotoTypes.BEFORE), None)
    latest_after = next((photo for photo in photos if photo.photo_type == BookingPhoto.PhotoTypes.AFTER), None)

    return {
        "booking": booking,
        "booking_photos": photos,
        "compare_before_photo": latest_before,
        "compare_after_photo": latest_after,
    }


def _parse_block_start_end(date_str, start_time_str, end_time_str):
    try:
        date_value = datetime.strptime(date_str, "%Y-%m-%d").date()
        start_time_value = datetime.strptime(start_time_str, "%H:%M").time()
        end_time_value = datetime.strptime(end_time_str, "%H:%M").time()
    except ValueError as exc:
        raise ValueError("Fecha u hora inválida.") from exc

    if end_time_value <= start_time_value:
        raise ValueError("La hora de fin debe ser posterior al inicio.")

    return date_value, start_time_value, end_time_value

@login_required
def booking_list(request):
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    source = request.GET.get("source", "").strip()

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

    if source:
        bookings = bookings.filter(source=source)

    context = {
        "active_section": "bookings",
        "bookings": bookings,
        "query": query,
        "status": status,
        "source": source,
        "bookings_count": bookings.count(),
        "status_choices": Booking.Statuses.choices,
        "source_choices": Booking.Sources.choices,
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
    start_at_param = request.GET.get("start_at", "").strip()
    employee_param = request.GET.get("employee", "").strip()

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
            "source": Booking.Sources.REBOOKING,
            "notes": "",
        }

    if request.method == "GET" and start_at_param:
        try:
            start_at = datetime.strptime(start_at_param, "%Y-%m-%dT%H:%M")
            end_at = start_at + timedelta(minutes=60)
            initial.update({
                "start_at": start_at.strftime("%Y-%m-%dT%H:%M"),
                "end_at": end_at.strftime("%Y-%m-%dT%H:%M"),
            })
        except ValueError:
            pass

    if request.method == "GET" and employee_param:
        try:
            initial["employee"] = Employee.objects.get(pk=employee_param, is_active=True)
        except (Employee.DoesNotExist, ValueError):
            pass

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
            "photo_form": None,
            "booking_photos": [],
            "referral_clients": Client.objects.filter(is_active=True).order_by("first_name", "last_name"),
        },
    )


@login_required
def booking_update(request, pk):
    booking = get_object_or_404(Booking, pk=pk)
    form = BookingForm(
        instance=booking,
        initial={
            "start_at": timezone.localtime(booking.start_at).strftime("%Y-%m-%dT%H:%M"),
            "end_at": timezone.localtime(booking.end_at).strftime("%Y-%m-%dT%H:%M"),
        }
    )
    photo_form = BookingPhotoForm()

    if request.method == "POST" and "photo_submit" in request.POST:
        photo_form = BookingPhotoForm(request.POST, request.FILES)
        if photo_form.is_valid():
            booking_photo = photo_form.save(commit=False)
            booking_photo.booking = booking
            booking_photo.client = booking.client
            booking_photo.save()
            messages.success(request, "Foto añadida al historial del cliente.")
            return redirect("bookings:update", pk=booking.pk)
    elif request.method == "POST":
        form = BookingForm(request.POST, instance=booking)
        if form.is_valid():
            booking = form.save()
            messages.success(request, f"Reserva actualizada: {booking}")
            return redirect("bookings:list")

    return render(
        request,
        "bookings/booking_form.html",
        {
            "active_section": "bookings",
            "form": form,
            "booking": booking,
            "is_edit": True,
            "photo_form": photo_form,
            **_build_booking_photo_context(booking),
            "referral_clients": Client.objects.filter(is_active=True).order_by("first_name", "last_name"),
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
def booking_photo_image(request, pk):
    photo = get_object_or_404(
        BookingPhoto.objects.select_related("booking", "client"),
        pk=pk,
    )
    guessed_type, _encoding = mimetypes.guess_type(photo.image.name)
    content_type = guessed_type or "application/octet-stream"
    return FileResponse(photo.image.open("rb"), content_type=content_type)


@login_required
def booking_photos_partial(request, pk):
    booking = get_object_or_404(
        Booking.objects.select_related("client", "employee", "service", "zone"),
        pk=pk,
    )
    html = render_to_string(
        "bookings/_photo_collection.html",
        _build_booking_photo_context(booking),
        request=request,
    )
    return JsonResponse({"ok": True, "html": html})


@login_required
@require_POST
def booking_photo_upload_api(request, pk):
    booking = get_object_or_404(Booking, pk=pk)
    form = BookingPhotoForm(request.POST, request.FILES)

    if not form.is_valid():
        message = "No se pudo subir la foto."
        for field_errors in form.errors.values():
            if field_errors:
                message = field_errors[0]
                break
        return JsonResponse({"ok": False, "message": message}, status=400)

    photo = form.save(commit=False)
    photo.booking = booking
    photo.client = booking.client
    photo.save()

    html = render_to_string(
        "bookings/_photo_collection.html",
        _build_booking_photo_context(booking),
        request=request,
    )
    return JsonResponse({"ok": True, "html": html})


@login_required
@require_POST
def booking_photo_delete(request, booking_pk, photo_pk):
    booking = get_object_or_404(Booking, pk=booking_pk)
    photo = get_object_or_404(BookingPhoto, pk=photo_pk, booking=booking)
    if photo.image:
        photo.image.delete(save=False)
    photo.delete()
    messages.success(request, "Foto eliminada del historial.")
    return redirect("bookings:update", pk=booking.pk)


@login_required
@require_POST
def booking_photo_delete_api(request, booking_pk, photo_pk):
    booking = get_object_or_404(Booking, pk=booking_pk)
    photo = get_object_or_404(BookingPhoto, pk=photo_pk, booking=booking)
    if photo.image:
        photo.image.delete(save=False)
    photo.delete()

    html = render_to_string(
        "bookings/_photo_collection.html",
        _build_booking_photo_context(booking),
        request=request,
    )
    return JsonResponse({"ok": True, "html": html})

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
    calendar_view = request.GET.get("view", "days").strip().lower()
    today = timezone.localdate()

    if calendar_view not in {"days", "team"}:
        calendar_view = "days"

    try:
        current_date = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else today
    except ValueError:
        current_date = today

    visible_days = [current_date, current_date + timedelta(days=1), current_date + timedelta(days=2)]
    bookings_by_day = {day: list(get_bookings_for_day(day)) for day in visible_days}
    all_bookings = [booking for day in visible_days for booking in bookings_by_day[day]]
    employees_by_id = {}
    employee_agenda_map = {}
    total_bookings = 0

    day_columns = []
    for day in visible_days:
        day_cards = []
        day_time_blocks = []
        for booking in bookings_by_day[day]:
            card = booking_layout_data(booking)
            card["target_id"] = f"booking-card-{booking.pk}"
            card["reschedule_date"] = timezone.localtime(booking.start_at).strftime("%Y-%m-%d")
            card["reschedule_time"] = timezone.localtime(booking.start_at).strftime("%H:%M")
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
                "service_color": card["service_color"],
                "status": card["status"],
                "status_label": card["status_label"],
            })

        for employee in Employee.objects.filter(is_active=True).order_by("first_name", "last_name"):
            for time_block in get_employee_time_blocks(employee, day):
                block_card = build_time_block_layout_data(time_block)
                day_time_blocks.append(block_card)

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
            "query_date": day.strftime("%Y-%m-%d"),
            "cards": day_cards,
            "time_blocks": day_time_blocks,
            "zones": sorted(zone_map.values(), key=lambda item: item["name"]),
        })

    employee_agenda = sorted(
        employee_agenda_map.values(),
        key=lambda item: (item["employee"].first_name, item["employee"].last_name),
    )
    for item in employee_agenda:
        item["bookings_count"] = len(item["bookings"])

    team_timelines = []
    day_bookings = bookings_by_day[current_date]
    active_employees = list(
        Employee.objects.filter(is_active=True).order_by("first_name", "last_name")
    )

    for employee in active_employees:
        schedule = get_employee_schedule(employee, current_date)
        employee_cards = []
        employee_time_blocks = []

        for booking in day_bookings:
            if booking.employee_id != employee.pk:
                continue

            card = booking_layout_data(booking)
            card["target_id"] = f"booking-card-{booking.pk}"
            card["reschedule_date"] = timezone.localtime(booking.start_at).strftime("%Y-%m-%d")
            card["reschedule_time"] = timezone.localtime(booking.start_at).strftime("%H:%M")
            employee_cards.append(card)

        for time_block in get_employee_time_blocks(employee, current_date):
            employee_time_blocks.append(build_time_block_layout_data(time_block))

        schedule_block = None
        break_block = None
        schedule_label = "Sin turno"

        if schedule:
            schedule_block = {
                "top": max(minutes_from_work_start(schedule["start_at"]), 0),
                "height": max(
                    int((schedule["end_at"] - schedule["start_at"]).total_seconds() // 60),
                    30,
                ),
            }
            schedule_label = (
                f"{timezone.localtime(schedule['start_at']).strftime('%H:%M')} - "
                f"{timezone.localtime(schedule['end_at']).strftime('%H:%M')}"
            )
            if schedule.get("label"):
                schedule_label = f"{schedule_label} · {schedule['label']}"

            if schedule.get("break_start_at") and schedule.get("break_end_at"):
                break_block = {
                    "top": max(minutes_from_work_start(schedule["break_start_at"]), 0),
                    "height": max(
                        int(
                            (
                                schedule["break_end_at"] - schedule["break_start_at"]
                            ).total_seconds()
                            // 60
                        ),
                        18,
                    ),
                    "label": schedule.get("break_label") or "Pausa",
                    "employee_id": employee.pk,
                    "date": current_date.strftime("%Y-%m-%d"),
                    "start_time": timezone.localtime(schedule["break_start_at"]).strftime("%H:%M"),
                    "end_time": timezone.localtime(schedule["break_end_at"]).strftime("%H:%M"),
                }

        team_timelines.append(
            {
                "employee": employee,
                "cards": employee_cards,
                "time_blocks": employee_time_blocks,
                "bookings_count": len(employee_cards),
                "schedule": schedule,
                "schedule_block": schedule_block,
                "break_block": break_block,
                "schedule_label": schedule_label,
                "is_day_off": schedule is None,
            }
        )

    context = {
        "active_section": "calendar",
        "calendar_view": calendar_view,
        "current_date": current_date,
        "hour_lines": build_calendar_hour_lines(),
        "calendar_height": (DEFAULT_WORK_END_HOUR - DEFAULT_WORK_START_HOUR) * 60,
        "prev_date": current_date - timedelta(days=1),
        "next_date": current_date + timedelta(days=1),
        "today": today,
        "day_columns": day_columns,
        "employee_agenda": employee_agenda,
        "team_timelines": team_timelines,
        "total_bookings": total_bookings,
        "status_choices": Booking.Statuses.choices,
        "calendar_employees": active_employees,
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
            "source": booking.source,
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


@login_required
@require_POST
def booking_status_api(request, pk):
    booking = get_object_or_404(Booking, pk=pk)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "message": "Payload inválido."}, status=400)

    status_value = payload.get("status")
    valid_statuses = {value for value, _label in Booking.Statuses.choices}

    if status_value not in valid_statuses:
        return JsonResponse({"ok": False, "message": "Estado inválido."}, status=400)

    booking.status = status_value
    booking.save(update_fields=["status", "updated_at"])

    return JsonResponse({
        "ok": True,
        "message": "Estado actualizado.",
        "status": booking.status,
        "status_label": booking.get_status_display(),
    })


@login_required
@require_POST
def booking_quick_status_update(request, pk):
    booking = get_object_or_404(Booking, pk=pk)
    status_value = (request.POST.get("status") or "").strip()
    allowed_statuses = {
        Booking.Statuses.DONE,
        Booking.Statuses.NO_SHOW,
    }

    if status_value not in allowed_statuses:
        messages.error(request, "Estado rápido no válido.")
        return redirect("dashboard:home")

    booking.status = status_value
    booking.save(update_fields=["status", "updated_at"])
    messages.success(
        request,
        f"Reserva actualizada: {booking.client} · {booking.get_status_display()}."
    )
    return redirect("dashboard:home")


@login_required
@require_POST
def calendar_time_block_create_api(request):
    from employees.models import EmployeeTimeBlock

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "message": "Payload inválido."}, status=400)

    employee_id = payload.get("employee_id")
    date_str = (payload.get("date") or "").strip()
    start_time_str = (payload.get("start_time") or "").strip()
    end_time_str = (payload.get("end_time") or "").strip()
    label = (payload.get("label") or "").strip() or "Bloqueo"
    color = (payload.get("color") or "").strip() or "#111111"
    repeat_pattern = (payload.get("repeat_pattern") or "none").strip()
    repeat_until_str = (payload.get("repeat_until") or "").strip()

    if not employee_id or not date_str or not start_time_str or not end_time_str:
        return JsonResponse({"ok": False, "message": "Faltan datos para crear el bloqueo."}, status=400)

    employee = get_object_or_404(Employee, pk=employee_id, is_active=True)

    try:
        date_value, start_time_value, end_time_value = _parse_block_start_end(
            date_str,
            start_time_str,
            end_time_str,
        )
    except ValueError as exc:
        return JsonResponse({"ok": False, "message": str(exc)}, status=400)

    repeat_until = date_value
    if repeat_until_str:
        try:
            repeat_until = datetime.strptime(repeat_until_str, "%Y-%m-%d").date()
        except ValueError:
            return JsonResponse({"ok": False, "message": "Fecha final inválida."}, status=400)
        if repeat_until < date_value:
            return JsonResponse({"ok": False, "message": "La fecha final debe ser posterior o igual a la inicial."}, status=400)

    if repeat_pattern not in {"none", "daily", "weekdays"}:
        repeat_pattern = "none"

    created = []
    current_date = date_value
    while current_date <= repeat_until:
        should_create = (
            repeat_pattern == "daily"
            or (repeat_pattern == "weekdays" and current_date.weekday() < 5)
            or (repeat_pattern == "none" and current_date == date_value)
        )
        if should_create:
            EmployeeTimeBlock.objects.create(
                employee=employee,
                date=current_date,
                start_time=start_time_value,
                end_time=end_time_value,
                label=label,
                color=color,
            )
            created.append(current_date.strftime("%Y-%m-%d"))

        if repeat_pattern == "none":
            break
        current_date += timedelta(days=1)

    return JsonResponse(
        {
            "ok": True,
            "message": "Bloqueo creado correctamente.",
            "created_dates": created,
        }
    )


@login_required
@require_POST
def calendar_time_block_update_api(request, pk):
    from employees.models import EmployeeTimeBlock

    time_block = get_object_or_404(EmployeeTimeBlock, pk=pk)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "message": "Payload inválido."}, status=400)

    employee_id = payload.get("employee_id")
    date_str = (payload.get("date") or "").strip()
    start_time_str = (payload.get("start_time") or "").strip()
    end_time_str = (payload.get("end_time") or "").strip()
    label = (payload.get("label") or "").strip() or "Bloqueo"
    color = (payload.get("color") or "").strip() or "#111111"

    if not employee_id or not date_str or not start_time_str or not end_time_str:
        return JsonResponse({"ok": False, "message": "Faltan datos para actualizar el bloqueo."}, status=400)

    employee = get_object_or_404(Employee, pk=employee_id, is_active=True)

    try:
        date_value, start_time_value, end_time_value = _parse_block_start_end(
            date_str,
            start_time_str,
            end_time_str,
        )
    except ValueError as exc:
        return JsonResponse({"ok": False, "message": str(exc)}, status=400)

    time_block.employee = employee
    time_block.date = date_value
    time_block.start_time = start_time_value
    time_block.end_time = end_time_value
    time_block.label = label
    time_block.color = color
    time_block.save()

    return JsonResponse({"ok": True, "message": "Bloqueo actualizado correctamente."})


@login_required
@require_POST
def calendar_time_block_delete_api(request, pk):
    from employees.models import EmployeeTimeBlock

    time_block = get_object_or_404(EmployeeTimeBlock, pk=pk)
    time_block.delete()
    return JsonResponse({"ok": True, "message": "Bloqueo eliminado correctamente."})


@login_required
@require_POST
def calendar_break_update_api(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "message": "Payload inválido."}, status=400)

    employee_id = payload.get("employee_id")
    date_str = (payload.get("date") or "").strip()
    clear_break = bool(payload.get("clear"))

    if not employee_id or not date_str:
        return JsonResponse({"ok": False, "message": "Faltan datos de empleado o fecha."}, status=400)

    employee = get_object_or_404(Employee, pk=employee_id, is_active=True)

    try:
        date_value = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return JsonResponse({"ok": False, "message": "Fecha inválida."}, status=400)

    schedule = get_employee_schedule(employee, date_value)
    if not schedule:
        return JsonResponse({"ok": False, "message": "No hay turno para esta fecha."}, status=400)

    override, _created = EmployeeScheduleOverride.objects.get_or_create(
        employee=employee,
        date=date_value,
        defaults={
            "is_day_off": False,
            "start_time": timezone.localtime(schedule["start_at"]).time(),
            "end_time": timezone.localtime(schedule["end_at"]).time(),
            "break_start": None,
            "break_end": None,
            "break_label": "",
            "label": "Ajuste calendario",
        },
    )

    if clear_break:
        override.break_start = None
        override.break_end = None
        override.break_label = ""
    else:
        start_time_str = (payload.get("start_time") or "").strip()
        end_time_str = (payload.get("end_time") or "").strip()
        break_label = (payload.get("label") or "").strip() or "Pausa"

        if not start_time_str or not end_time_str:
            return JsonResponse({"ok": False, "message": "Indica inicio y fin de la pausa."}, status=400)

        try:
            _date_value, start_time_value, end_time_value = _parse_block_start_end(
                date_str,
                start_time_str,
                end_time_str,
            )
        except ValueError as exc:
            return JsonResponse({"ok": False, "message": str(exc)}, status=400)

        shift_start = override.start_time or timezone.localtime(schedule["start_at"]).time()
        shift_end = override.end_time or timezone.localtime(schedule["end_at"]).time()

        if start_time_value <= shift_start or end_time_value >= shift_end:
            return JsonResponse({"ok": False, "message": "La pausa debe quedar dentro del turno."}, status=400)

        override.break_start = start_time_value
        override.break_end = end_time_value
        override.break_label = break_label

    override.is_day_off = False
    override.start_time = override.start_time or timezone.localtime(schedule["start_at"]).time()
    override.end_time = override.end_time or timezone.localtime(schedule["end_at"]).time()
    override.save()

    return JsonResponse({"ok": True, "message": "Pausa actualizada correctamente."})
