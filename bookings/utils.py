from datetime import datetime, timedelta, time
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from employees.models import EmployeeRecurringTimeBlock, EmployeeTimeBlock

from .models import Booking


DEFAULT_WORK_START_HOUR = 9
DEFAULT_WORK_END_HOUR = 20
SLOT_STEP_MINUTES = 30
MOBILE_SLOT_STEP_MINUTES = 15
CALENDAR_DAY_SPAN = 5  # сколько дней показывать сверху

SERVICE_COLOR_PALETTE = [
    "#f97316",
    "#14b8a6",
    "#ec4899",
    "#8b5cf6",
    "#22c55e",
    "#06b6d4",
    "#f59e0b",
    "#ef4444",
    "#84cc16",
    "#3b82f6",
]


def combine_local(date_obj, time_obj):
    return timezone.make_aware(datetime.combine(date_obj, time_obj))

def build_calendar_hour_lines():
    lines = []
    for hour in range(DEFAULT_WORK_START_HOUR, DEFAULT_WORK_END_HOUR):
        lines.append({
            "label": f"{hour:02d}:00",
            "top": (hour - DEFAULT_WORK_START_HOUR) * 60,
        })
    return lines

def get_day_bounds(date_obj):
    day_start = combine_local(date_obj, time(hour=0, minute=0))
    day_end = combine_local(date_obj, time(hour=23, minute=59, second=59))
    return day_start, day_end


def get_work_bounds(date_obj):
    start = combine_local(date_obj, time(hour=DEFAULT_WORK_START_HOUR, minute=0))
    end = combine_local(date_obj, time(hour=DEFAULT_WORK_END_HOUR, minute=0))
    return start, end


def get_employee_schedule(employee, date_obj):
    shift = employee.get_shift_for_date(date_obj)

    if not shift:
        if date_obj.weekday() == 6:
            return None
        return {
            "start_at": combine_local(date_obj, time(hour=DEFAULT_WORK_START_HOUR)),
            "end_at": combine_local(date_obj, time(hour=DEFAULT_WORK_END_HOUR)),
            "break_start_at": None,
            "break_end_at": None,
            "label": "Horario general",
            "is_override": False,
            "is_day_off": False,
        }

    if shift.is_day_off or not shift.start_time or not shift.end_time:
        return None

    return {
        "start_at": combine_local(date_obj, shift.start_time),
        "end_at": combine_local(date_obj, shift.end_time),
        "break_start_at": combine_local(date_obj, shift.break_start) if shift.break_start else None,
        "break_end_at": combine_local(date_obj, shift.break_end) if shift.break_end else None,
        "break_label": getattr(shift, "break_label", "") or "Pausa",
        "label": getattr(shift, "label", "") or getattr(shift, "note", ""),
        "is_override": hasattr(shift, "date"),
        "is_day_off": False,
    }


def fits_employee_schedule(employee, start_at, end_at):
    local_start = timezone.localtime(start_at)
    local_end = timezone.localtime(end_at)

    if local_start.date() != local_end.date():
        return False, "La reserva debe empezar y terminar el mismo día."

    schedule = get_employee_schedule(employee, local_start.date())
    if not schedule:
        return False, "El empleado no trabaja ese día."

    if start_at < schedule["start_at"] or end_at > schedule["end_at"]:
        return False, "La reserva queda fuera del turno del empleado."

    break_start = schedule["break_start_at"]
    break_end = schedule["break_end_at"]
    if break_start and break_end and overlaps(start_at, end_at, break_start, break_end):
        return False, "La reserva cae dentro de la pausa del empleado."

    for block in get_employee_time_block_occurrences(employee, local_start.date()):
        block_start = combine_local(local_start.date(), block["start_time"])
        block_end = combine_local(local_start.date(), block["end_time"])
        if overlaps(start_at, end_at, block_start, block_end):
            label = block["label"] or "bloqueo horario"
            return False, f"La reserva cae dentro de un bloqueo del empleado: {label}."

    return True, ""


def overlaps(start_a, end_a, start_b, end_b):
    return start_a < end_b and end_a > start_b


def get_employee_time_blocks(employee, date_obj):
    return list(
        employee.time_blocks.filter(date=date_obj).order_by("start_time", "end_time", "pk")
    )


def get_employee_recurring_time_blocks(employee, date_obj):
    return list(
        employee.recurring_time_blocks.filter(
            active=True,
            weekday=date_obj.weekday(),
            date_from__lte=date_obj,
        )
        .filter(Q(date_to__isnull=True) | Q(date_to__gte=date_obj))
        .order_by("start_time", "end_time", "pk")
    )


def get_employee_time_block_occurrences(employee, date_obj):
    occurrences = []
    for block in get_employee_time_blocks(employee, date_obj):
        occurrences.append(
            {
                "id": block.pk,
                "employee": block.employee,
                "employee_id": block.employee_id,
                "date": block.date,
                "start_time": block.start_time,
                "end_time": block.end_time,
                "label": block.label,
                "color": block.color,
                "is_recurring": False,
                "recurring_id": None,
                "editable": True,
            }
        )
    for block in get_employee_recurring_time_blocks(employee, date_obj):
        occurrences.append(
            {
                "id": f"recurring-{block.pk}",
                "employee": block.employee,
                "employee_id": block.employee_id,
                "date": date_obj,
                "start_time": block.start_time,
                "end_time": block.end_time,
                "label": block.label,
                "color": block.color,
                "is_recurring": True,
                "recurring_id": block.pk,
                "editable": True,
            }
        )
    return sorted(occurrences, key=lambda item: (item["start_time"], item["end_time"], str(item["id"])))


def time_block_conflicts(employee, date_obj, start_time, end_time, exclude_time_block_id=None):
    one_time_conflict = EmployeeTimeBlock.objects.filter(
        employee=employee,
        date=date_obj,
        start_time__lt=end_time,
        end_time__gt=start_time,
    )
    if exclude_time_block_id:
        one_time_conflict = one_time_conflict.exclude(pk=exclude_time_block_id)
    if one_time_conflict.exists():
        return True

    return EmployeeRecurringTimeBlock.objects.filter(
        active=True,
        employee=employee,
        weekday=date_obj.weekday(),
        date_from__lte=date_obj,
        start_time__lt=end_time,
        end_time__gt=start_time,
    ).filter(Q(date_to__isnull=True) | Q(date_to__gte=date_obj)).exists()


def recurring_time_block_conflicts(employee, weekday, start_time, end_time, date_from, date_to=None, exclude_recurring_id=None):
    recurring_conflict = EmployeeRecurringTimeBlock.objects.filter(
        active=True,
        employee=employee,
        weekday=weekday,
        start_time__lt=end_time,
        end_time__gt=start_time,
    )
    if exclude_recurring_id:
        recurring_conflict = recurring_conflict.exclude(pk=exclude_recurring_id)
    if date_to:
        recurring_conflict = recurring_conflict.filter(date_from__lte=date_to)
    recurring_conflict = recurring_conflict.filter(Q(date_to__isnull=True) | Q(date_to__gte=date_from))
    return recurring_conflict.exists()


def is_slot_available(employee, service, zone, start_at, end_at, exclude_booking_id=None):
    fits_schedule, _message = fits_employee_schedule(employee, start_at, end_at)
    if not fits_schedule:
        return False

    qs = Booking.objects.exclude(status=Booking.Statuses.CANCELLED)

    if exclude_booking_id:
        qs = qs.exclude(pk=exclude_booking_id)

    employee_conflict = qs.filter(
        employee=employee,
        start_at__lt=end_at,
        end_at__gt=start_at,
    ).exists()

    if employee_conflict:
        return False

    if time_block_conflicts(
        employee,
        timezone.localtime(start_at).date(),
        timezone.localtime(start_at).time(),
        timezone.localtime(end_at).time(),
    ):
        return False

    if service.requires_zone and zone is None:
        return find_available_zone(service, start_at, end_at, exclude_booking_id=exclude_booking_id) is not None

    if service.requires_zone and zone:
        zone_conflict = qs.filter(
            zone=zone,
            start_at__lt=end_at,
            end_at__gt=start_at,
        ).exists()

        if zone_conflict:
            return False

    return True


def find_available_zone(service, start_at, end_at, exclude_booking_id=None):
    if not service.requires_zone:
        return None
    booking_qs = Booking.objects.exclude(status=Booking.Statuses.CANCELLED)
    if exclude_booking_id:
        booking_qs = booking_qs.exclude(pk=exclude_booking_id)
    for zone in service.allowed_zones.filter(is_active=True).order_by("name", "pk"):
        conflict = booking_qs.filter(
            zone=zone,
            start_at__lt=end_at,
            end_at__gt=start_at,
        ).exists()
        if not conflict:
            return zone
    return None


def find_available_slots_for_day(date_obj, employee, service, zone=None, exclude_booking_id=None):
    slots, _blocked = build_available_slots_for_day(
        date_obj=date_obj,
        employee=employee,
        service=service,
        zone=zone,
        exclude_booking_id=exclude_booking_id,
        step_minutes=SLOT_STEP_MINUTES,
    )
    return slots


def build_available_slots_for_day(date_obj, employee, service, zone=None, exclude_booking_id=None, step_minutes=SLOT_STEP_MINUTES):
    schedule = get_employee_schedule(employee, date_obj)
    default_start, default_end = get_work_bounds(date_obj)
    if not schedule:
        return [], [
            {
                "start_at": default_start,
                "end_at": default_end,
                "reason": "Fuera de horario",
            }
        ]

    work_start = schedule["start_at"]
    work_end = schedule["end_at"]
    break_start = schedule["break_start_at"]
    break_end = schedule["break_end_at"]
    time_blocks = get_employee_time_block_occurrences(employee, date_obj)
    duration = timedelta(minutes=service.duration_minutes)
    step = timedelta(minutes=step_minutes)
    day_start, day_end = get_day_bounds(date_obj)

    slots = []
    blocked = []

    if default_start < work_start:
        blocked.append({"start_at": default_start, "end_at": work_start, "reason": "Fuera de horario"})
    if work_end < default_end:
        blocked.append({"start_at": work_end, "end_at": default_end, "reason": "Fuera de horario"})

    if break_start and break_end:
        blocked.append(
            {
                "start_at": break_start,
                "end_at": break_end,
                "reason": schedule.get("break_label") or "Pausa",
            }
        )

    for item in time_blocks:
        blocked.append(
            {
                "start_at": combine_local(date_obj, item["start_time"]),
                "end_at": combine_local(date_obj, item["end_time"]),
                "reason": item["label"] or "Bloqueo",
            }
        )

    booking_qs = Booking.objects.exclude(status=Booking.Statuses.CANCELLED)
    if exclude_booking_id:
        booking_qs = booking_qs.exclude(pk=exclude_booking_id)

    for booking in booking_qs.filter(employee=employee, start_at__lt=day_end, end_at__gt=day_start).order_by("start_at", "pk"):
        blocked.append(
            {
                "start_at": max(booking.start_at, day_start),
                "end_at": min(booking.end_at, day_end),
                "reason": "Reserva",
            }
        )

    if service.requires_zone and zone:
        for booking in (
            booking_qs.filter(zone=zone, start_at__lt=day_end, end_at__gt=day_start)
            .exclude(employee=employee)
            .order_by("start_at", "pk")
        ):
            blocked.append(
                {
                    "start_at": max(booking.start_at, day_start),
                    "end_at": min(booking.end_at, day_end),
                    "reason": "Zona ocupada",
                }
            )

    current = work_start

    while current + duration <= work_end:
        slot_end = current + duration

        if break_start and break_end and overlaps(current, slot_end, break_start, break_end):
            current += step
            continue

        blocked_by_time_block = any(
            overlaps(
                current,
                slot_end,
                combine_local(date_obj, item["start_time"]),
                combine_local(date_obj, item["end_time"]),
            )
            for item in time_blocks
        )
        if blocked_by_time_block:
            current += step
            continue

        if service.requires_zone and zone is None:
            if find_available_zone(service, current, slot_end, exclude_booking_id=exclude_booking_id) is None:
                current += step
                continue

        if is_slot_available(
            employee=employee,
            service=service,
            zone=zone,
            start_at=current,
            end_at=slot_end,
            exclude_booking_id=exclude_booking_id,
        ):
            slots.append({
                "start_at": current,
                "end_at": slot_end,
            })

        current += step

    blocked.sort(key=lambda item: (item["start_at"], item["end_at"], item["reason"]))
    return slots, blocked


def find_available_slots_nearby(start_date, employee, service, zone=None, days_before=2, days_after=3, exclude_booking_id=None):
    results = []

    for offset in range(-days_before, days_after + 1):
        date_obj = start_date + timedelta(days=offset)
        day_slots = find_available_slots_for_day(
            date_obj=date_obj,
            employee=employee,
            service=service,
            zone=zone,
            exclude_booking_id=exclude_booking_id,
        )
        results.append({
            "date": date_obj,
            "slots": day_slots,
        })

    return results


def build_time_labels():
    labels = []
    for hour in range(DEFAULT_WORK_START_HOUR, DEFAULT_WORK_END_HOUR + 1):
        labels.append(time(hour=hour, minute=0))
    return labels


def get_calendar_days(center_date, days_before=2, days_after=2):
    days = []
    for offset in range(-days_before, days_after + 1):
        d = center_date + timedelta(days=offset)
        days.append(d)
    return days


def get_bookings_for_day(date_obj):
    day_start, day_end = get_day_bounds(date_obj)
    return (
        Booking.objects
        .select_related("client", "employee", "service", "zone")
        .prefetch_related("online_payments", "prepayment")
        .filter(start_at__lte=day_end, end_at__gte=day_start)
        .exclude(status=Booking.Statuses.CANCELLED)
        .order_by("start_at")
    )


def build_time_block_layout_data(block):
    start_at = combine_local(block.date, block.start_time)
    end_at = combine_local(block.date, block.end_time)
    start_minutes = minutes_from_work_start(start_at)
    duration_minutes = int((end_at - start_at).total_seconds() // 60)

    return {
        "id": f"time-block-{block.pk}",
        "pk": block.pk,
        "employee_id": block.employee_id,
        "employee_name": getattr(block.employee, "full_name", str(block.employee)),
        "label": block.label or "Bloqueo",
        "color": block.color or "#111111",
        "start_at": timezone.localtime(start_at),
        "end_at": timezone.localtime(end_at),
        "top": max(start_minutes, 0),
        "height": max(duration_minutes, 18),
    }


def minutes_from_work_start(dt):
    local_dt = timezone.localtime(dt)
    return (local_dt.hour * 60 + local_dt.minute) - (DEFAULT_WORK_START_HOUR * 60)


def service_calendar_color(service_id):
    if not service_id:
        return SERVICE_COLOR_PALETTE[0]
    return SERVICE_COLOR_PALETTE[(service_id - 1) % len(SERVICE_COLOR_PALETTE)]

    
def booking_payment_summary(booking):
    total_amount = booking.client_price_snapshot or booking.price_snapshot or Decimal("0.00")
    paid_amount = booking.paid_amount
    due_amount = max(total_amount - paid_amount, Decimal("0.00"))
    prepayment = getattr(booking, "prepayment", None)

    if total_amount <= Decimal("0.00"):
        label, status_class = "", ""
    elif due_amount <= Decimal("0.00"):
        label, status_class = "Pagado completo", "is-paid"
    elif prepayment and paid_amount > Decimal("0.00"):
        label, status_class = f"Señal pagada · quedan {due_amount} €", "is-partial"
    elif paid_amount > Decimal("0.00"):
        label, status_class = f"Pagado {paid_amount} € · quedan {due_amount} €", "is-partial"
    else:
        label, status_class = f"Sin pago · {due_amount} € pendiente", "is-unpaid"

    return {
        "total_amount": total_amount,
        "paid_amount": paid_amount,
        "due_amount": due_amount,
        "label": label,
        "status_class": status_class,
    }


def booking_layout_data(booking):
    start_minutes = minutes_from_work_start(booking.start_at)
    duration_minutes = int((booking.end_at - booking.start_at).total_seconds() // 60)
    payment_summary = booking_payment_summary(booking)

    return {
        "id": booking.id,
        "client": str(booking.client),
        "client_id": booking.client_id,
        "employee": str(booking.employee),
        "employee_id": booking.employee_id,
        "employee_color": booking.employee.calendar_color or "#c75c8b",
        "service": str(booking.service),
        "service_id": booking.service_id,
        "service_color": service_calendar_color(booking.service_id),
        "zone": str(booking.zone) if booking.zone else "—",
        "zone_id": booking.zone_id,
        "zone_color": booking.zone.color if booking.zone else "#d8c7cf",
        "status": booking.status,
        "status_label": booking.get_status_display(),
        "source": booking.source,
        "source_label": booking.get_source_display(),
        "notes": booking.notes,
        "start_at": timezone.localtime(booking.start_at),
        "end_at": timezone.localtime(booking.end_at),
        "top": max(start_minutes, 0),
        "height": max(duration_minutes, 30),
        "payment_label": payment_summary["label"],
        "payment_status_class": payment_summary["status_class"],
    }
