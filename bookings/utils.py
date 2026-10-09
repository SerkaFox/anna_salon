from datetime import datetime, timedelta, time
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from employees.models import EmployeeRecurringTimeBlock, EmployeeTimeBlock

from .models import Booking


DEFAULT_WORK_START_HOUR = 9
DEFAULT_WORK_END_HOUR = 21
SLOT_STEP_MINUTES = 30
MOBILE_SLOT_STEP_MINUTES = 15
CALENDAR_PIXELS_PER_MINUTE = 1.5
CALENDAR_DAY_SPAN = 5  # сколько дней показывать сверху
PUBLIC_BOOKING_MAX_DAYS_AHEAD = 366

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
    total_minutes = (DEFAULT_WORK_END_HOUR - DEFAULT_WORK_START_HOUR) * 60
    for minute_offset in range(0, total_minutes, 30):
        hour = DEFAULT_WORK_START_HOUR + minute_offset // 60
        minute = minute_offset % 60
        lines.append({
            "label": f"{hour:02d}:{minute:02d}",
            "top": calendar_pixels(minute_offset),
            "is_hour": minute == 0,
        })
    return lines


def calendar_pixels(minutes):
    return round(minutes * CALENDAR_PIXELS_PER_MINUTE)

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


def fits_employee_schedule(employee, start_at, end_at, allow_outside_schedule=False):
    local_start = timezone.localtime(start_at)
    local_end = timezone.localtime(end_at)

    if local_start.date() != local_end.date():
        return False, "La reserva debe empezar y terminar el mismo día."

    schedule = get_employee_schedule(employee, local_start.date())
    if not schedule and not allow_outside_schedule:
        return False, "El empleado no trabaja ese día."

    if schedule and not allow_outside_schedule and (
        start_at < schedule["start_at"] or end_at > schedule["end_at"]
    ):
        return False, "La reserva queda fuera del turno del empleado."

    break_start = schedule["break_start_at"] if schedule else None
    break_end = schedule["break_end_at"] if schedule else None
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
                "note": block.note,
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
                "note": block.note,
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


def is_slot_available(employee, service, zone, start_at, end_at, exclude_booking_id=None, allow_outside_schedule=False):
    fits_schedule, _message = fits_employee_schedule(
        employee,
        start_at,
        end_at,
        allow_outside_schedule=allow_outside_schedule,
    )
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
        return find_available_zone(
            service,
            start_at,
            end_at,
            exclude_booking_id=exclude_booking_id,
            employee=employee,
        ) is not None

    if service.requires_zone and zone:
        if employee.zones.exists() and not employee.zones.filter(pk=zone.pk).exists():
            return False
        zone_conflict = qs.filter(
            zone=zone,
            start_at__lt=end_at,
            end_at__gt=start_at,
        ).exists()

        if zone_conflict:
            return False

    return True


def find_available_zone(
    service,
    start_at,
    end_at,
    exclude_booking_id=None,
    employee=None,
):
    if not service.requires_zone:
        return None
    booking_qs = Booking.objects.exclude(status=Booking.Statuses.CANCELLED)
    if exclude_booking_id:
        booking_qs = booking_qs.exclude(pk=exclude_booking_id)
    zones = service.allowed_zones.filter(is_active=True)
    if employee is not None:
        zones = zones.filter(employees=employee)
    for zone in zones.order_by("name", "pk"):
        conflict = booking_qs.filter(
            zone=zone,
            start_at__lt=end_at,
            end_at__gt=start_at,
        ).exists()
        if not conflict:
            return zone
    return None


def exact_duplicate_bookings(booking):
    """Return records that describe the same client appointment exactly."""
    return Booking.objects.filter(
        client_id=booking.client_id,
        service_id=booking.service_id,
        start_at=booking.start_at,
        end_at=booking.end_at,
    )


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


def build_available_slots_for_day(date_obj, employee, service, zone=None, exclude_booking_id=None, step_minutes=SLOT_STEP_MINUTES, duration_minutes=None, allow_outside_schedule=False):
    schedule = get_employee_schedule(employee, date_obj)
    default_start, default_end = get_work_bounds(date_obj)
    if not schedule and not allow_outside_schedule:
        return [], [
            {
                "start_at": default_start,
                "end_at": default_end,
                "reason": "Fuera de horario",
            }
        ]

    work_start = default_start if allow_outside_schedule else schedule["start_at"]
    work_end = default_end if allow_outside_schedule else schedule["end_at"]
    break_start = schedule["break_start_at"] if schedule else None
    break_end = schedule["break_end_at"] if schedule else None
    time_blocks = get_employee_time_block_occurrences(employee, date_obj)
    duration = timedelta(minutes=duration_minutes or service.duration_minutes)
    step = timedelta(minutes=step_minutes)
    day_start, day_end = get_day_bounds(date_obj)

    slots = []
    blocked = []

    if schedule and default_start < schedule["start_at"]:
        blocked.append({"start_at": default_start, "end_at": schedule["start_at"], "reason": "Fuera de horario"})
    if schedule and schedule["end_at"] < default_end:
        blocked.append({"start_at": schedule["end_at"], "end_at": default_end, "reason": "Fuera de horario"})

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
            if find_available_zone(
                service,
                current,
                slot_end,
                exclude_booking_id=exclude_booking_id,
                employee=employee,
            ) is None:
                current += step
                continue

        if is_slot_available(
            employee=employee,
            service=service,
            zone=zone,
            start_at=current,
            end_at=slot_end,
            exclude_booking_id=exclude_booking_id,
            allow_outside_schedule=allow_outside_schedule,
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
        .prefetch_related("online_payments", "prepayment", "payments")
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
        "top": calendar_pixels(max(start_minutes, 0)),
        "height": max(calendar_pixels(duration_minutes), calendar_pixels(18)),
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

    all_online_payments = list(booking.online_payments.all())
    paid_statuses = {"paid", "partially_paid", "partially_refunded", "refund_pending"}
    online_payments = [payment for payment in all_online_payments if payment.status in paid_statuses]
    online_paid = sum(
        (max(payment.amount - payment.amount_refunded, Decimal("0.00")) for payment in online_payments),
        Decimal("0.00"),
    )

    manual_payments = list(booking.payments.all())
    manual_paid = sum((payment.signed_amount for payment in manual_payments), Decimal("0.00"))

    paid_amount = max(online_paid + manual_paid, Decimal("0.00"))
    due_amount = max(total_amount - paid_amount, Decimal("0.00"))
    prepayment = getattr(booking, "prepayment", None)

    methods = set()
    if online_payments:
        methods.add("tarjeta online")
    for payment in manual_payments:
        if payment.entry_type == payment.EntryTypes.PAYMENT:
            methods.add(payment.get_method_display().lower())
    method_suffix = f" ({', '.join(sorted(methods))})" if methods and paid_amount > Decimal("0.00") else ""

    has_refund = any(
        payment.status in {"refunded", "partially_refunded", "refund_pending"}
        or payment.amount_refunded > Decimal("0.00")
        for payment in all_online_payments
    ) or any(payment.entry_type == payment.EntryTypes.REFUND for payment in manual_payments)

    if total_amount <= Decimal("0.00"):
        state, label, status_class = "paid", "Pagado completo", "is-paid"
    elif due_amount <= Decimal("0.00"):
        state, label, status_class = "paid", f"Pagado completo{method_suffix}", "is-paid"
    elif prepayment and paid_amount > Decimal("0.00") and paid_amount == online_paid:
        state, label, status_class = "deposit", f"Pagado con señal{method_suffix} · quedan {due_amount} €", "is-partial"
    elif paid_amount > Decimal("0.00"):
        state, label, status_class = "deposit", f"Pago parcial {paid_amount} €{method_suffix} · quedan {due_amount} €", "is-partial"
    elif has_refund:
        state, label, status_class = "refunded", "Devuelto", "is-refunded"
    else:
        state, label, status_class = "unpaid", f"Sin pagar · {due_amount} € pendiente", "is-unpaid"

    return {
        "state": state,
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
        "top": calendar_pixels(max(start_minutes, 0)),
        "height": max(calendar_pixels(duration_minutes), calendar_pixels(30)),
        "payment_label": payment_summary["label"],
        "payment_status_class": payment_summary["status_class"],
    }


# ---------------------------------------------------------------------------
# Multi-service consecutive slot finder
# ---------------------------------------------------------------------------

def get_month_available_dates(year, month, services):
    """
    Return ISO date strings in year/month that have at least one genuinely bookable
    slot for these services. Uses bulk queries (≤7 total) + in-memory conflict check
    including employee bookings and zone conflicts.
    """
    import calendar as _cal
    from datetime import date as _date
    from employees.models import Employee as _Employee
    from employees.models import (
        EmployeeScheduleOverride,
        EmployeeWeeklyShift,
        EmployeeTimeBlock,
        EmployeeRecurringTimeBlock,
    )

    first_day = _date(year, month, 1)
    last_day_num = _cal.monthrange(year, month)[1]
    last_day = _date(year, month, last_day_num)

    emp_ids = set()
    service_emp_ids = {}
    for service in services:
        ids = list(
            _Employee.objects.filter(is_active=True, services=service)
            .values_list("pk", flat=True)
        )
        service_emp_ids[service.pk] = ids
        emp_ids.update(ids)
    emp_ids = list(emp_ids)
    if not emp_ids:
        return []

    weekly = {}
    for s in EmployeeWeeklyShift.objects.filter(employee_id__in=emp_ids).values(
        "employee_id", "weekday", "is_day_off", "start_time", "end_time", "break_start", "break_end"
    ):
        weekly.setdefault(s["employee_id"], {})[s["weekday"]] = s

    overrides = {}
    for ov in EmployeeScheduleOverride.objects.filter(
        employee_id__in=emp_ids, date__gte=first_day, date__lte=last_day
    ).values("employee_id", "date", "is_day_off", "start_time", "end_time", "break_start", "break_end"):
        overrides[(ov["employee_id"], ov["date"])] = ov

    # Bulk load employee booking conflicts: {(emp_id, date): [(start, end), …]}
    emp_day_bookings = {}
    for b in Booking.objects.filter(
        employee_id__in=emp_ids,
        start_at__date__gte=first_day,
        start_at__date__lte=last_day,
    ).exclude(status=Booking.Statuses.CANCELLED).values("employee_id", "start_at", "end_at"):
        d_key = timezone.localtime(b["start_at"]).date()
        emp_day_bookings.setdefault((b["employee_id"], d_key), []).append(
            (b["start_at"], b["end_at"])
        )

    # Bulk load zone booking conflicts for services that require a zone
    zone_day_bookings = {}
    all_zone_ids = set()
    service_zone_ids = {}  # {service_pk: [zone_pk, …]}
    for service in services:
        if service.requires_zone:
            zids = [z.pk for z in service.allowed_zones.all()]
            service_zone_ids[service.pk] = zids
            all_zone_ids.update(zids)
    if all_zone_ids:
        for b in Booking.objects.filter(
            zone_id__in=all_zone_ids,
            start_at__date__gte=first_day,
            start_at__date__lte=last_day,
        ).exclude(status=Booking.Statuses.CANCELLED).values("zone_id", "start_at", "end_at"):
            d_key = timezone.localtime(b["start_at"]).date()
            zone_day_bookings.setdefault((b["zone_id"], d_key), []).append(
                (b["start_at"], b["end_at"])
            )

    # Employee → allowed zone ids (empty set = all zones allowed)
    emp_zone_ids = {}
    for row in _Employee.objects.filter(pk__in=emp_ids).prefetch_related("zones"):
        emp_zone_ids[row.pk] = set(z.pk for z in row.zones.all())

    # Bulk load one-off time blocks: {(emp_id, date): [(start_time, end_time), …]}
    emp_time_blocks = {}
    for tb in EmployeeTimeBlock.objects.filter(
        employee_id__in=emp_ids, date__gte=first_day, date__lte=last_day
    ).values("employee_id", "date", "start_time", "end_time"):
        emp_time_blocks.setdefault((tb["employee_id"], tb["date"]), []).append(
            (tb["start_time"], tb["end_time"])
        )

    # Bulk load recurring time blocks: {emp_id: [(weekday, start_time, end_time, date_from, date_to), …]}
    emp_recurring_blocks = {}
    for rb in EmployeeRecurringTimeBlock.objects.filter(
        employee_id__in=emp_ids, active=True
    ).values("employee_id", "weekday", "start_time", "end_time", "date_from", "date_to"):
        emp_recurring_blocks.setdefault(rb["employee_id"], []).append(rb)

    today_local = timezone.localdate()
    now_aware = timezone.now()
    min_duration = timedelta(minutes=min(s.duration_minutes for s in services))
    step = timedelta(minutes=MOBILE_SLOT_STEP_MINUTES)
    max_date = today_local + timedelta(days=PUBLIC_BOOKING_MAX_DAYS_AHEAD)

    def _get_shift(emp_id, d):
        """Return (start_time, end_time, break_start, break_end) or None."""
        key = (emp_id, d)
        if key in overrides:
            ov = overrides[key]
            if ov["is_day_off"] or not ov["start_time"] or not ov["end_time"]:
                return None
            return ov["start_time"], ov["end_time"], ov["break_start"], ov["break_end"]
        emp_weekly = weekly.get(emp_id, {})
        if d.weekday() in emp_weekly:
            s = emp_weekly[d.weekday()]
            if s["is_day_off"] or not s["start_time"] or not s["end_time"]:
                return None
            return s["start_time"], s["end_time"], s["break_start"], s["break_end"]
        if d.weekday() == 6:
            return None
        return time(hour=DEFAULT_WORK_START_HOUR), time(hour=DEFAULT_WORK_END_HOUR), None, None

    def _zone_free(zone_id, d, t, t_end):
        return not any(s < t_end and e > t for s, e in zone_day_bookings.get((zone_id, d), []))

    def _time_block_conflicts(emp_id, d, t, t_end):
        # One-off blocks
        for tb_start, tb_end in emp_time_blocks.get((emp_id, d), []):
            bs = combine_local(d, tb_start)
            be = combine_local(d, tb_end)
            if bs < t_end and be > t:
                return True
        # Recurring blocks
        for rb in emp_recurring_blocks.get(emp_id, []):
            if rb["weekday"] != d.weekday():
                continue
            if d < rb["date_from"]:
                continue
            if rb["date_to"] and d > rb["date_to"]:
                continue
            bs = combine_local(d, rb["start_time"])
            be = combine_local(d, rb["end_time"])
            if bs < t_end and be > t:
                return True
        return False

    def emp_has_free_slot(emp_id, d, service):
        shift = _get_shift(emp_id, d)
        if shift is None:
            return False
        shift_start = combine_local(d, shift[0])
        shift_end = combine_local(d, shift[1])
        break_start = combine_local(d, shift[2]) if shift[2] else None
        break_end = combine_local(d, shift[3]) if shift[3] else None
        t = max(shift_start, now_aware) if d == today_local else shift_start
        duration = timedelta(minutes=service.duration_minutes)
        if shift_end - t < duration:
            return False
        day_bookings = emp_day_bookings.get((emp_id, d), [])
        requires_zone = service.requires_zone
        if requires_zone:
            svc_zones = service_zone_ids.get(service.pk, [])
            emp_zones = emp_zone_ids.get(emp_id, set())
            usable_zones = [z for z in svc_zones if not emp_zones or z in emp_zones]
            if not usable_zones:
                return False
        while t + duration <= shift_end:
            t_end = t + duration
            emp_conflict = any(s < t_end and e > t for s, e in day_bookings)
            break_conflict = bool(break_start and break_end and break_start < t_end and break_end > t)
            if not emp_conflict and not break_conflict and not _time_block_conflicts(emp_id, d, t, t_end):
                if not requires_zone or any(_zone_free(z, d, t, t_end) for z in usable_zones):
                    return True
            t += step
        return False

    available = []
    d = first_day
    while d <= last_day:
        if today_local <= d <= max_date:
            for service in services:
                if any(emp_has_free_slot(eid, d, service) for eid in service_emp_ids.get(service.pk, [])):
                    available.append(d.isoformat())
                    break
        d += timedelta(days=1)
    return available


def find_multi_service_slots(date_obj, services, step_minutes=MOBILE_SLOT_STEP_MINUTES, max_results=None):
    """
    Return time blocks on date_obj where all services can be scheduled back-to-back.
    Each block is a dict with start_at, label, end_at, total_duration_minutes, items.
    """
    if not services:
        return []

    from employees.models import Employee as _Employee

    total_minutes = sum(s.duration_minutes for s in services)
    work_start, work_end = get_work_bounds(date_obj)

    service_employees = {}
    for service in services:
        service_employees[service.pk] = list(
            _Employee.objects.filter(is_active=True, services=service)
            .order_by("first_name", "last_name")
        )

    results = []
    t = work_start
    step = timedelta(minutes=step_minutes)
    now = timezone.now()

    if len(services) == 1:
        # Single service: return one block per available employee per slot so
        # the frontend can display employee columns side-by-side.
        service = services[0]
        duration = timedelta(minutes=service.duration_minutes)
        while t + duration <= work_end:
            if t > now:
                for employee in service_employees[service.pk]:
                    end_t = t + duration
                    if not is_slot_available(employee, service, None, t, end_t):
                        continue
                    zone = (
                        find_available_zone(service, t, end_t, employee=employee)
                        if service.requires_zone
                        else None
                    )
                    if service.requires_zone and zone is None:
                        continue
                    local_t = timezone.localtime(t)
                    results.append({
                        "start_at": t.isoformat(),
                        "label": local_t.strftime("%H:%M"),
                        "end_at": end_t.isoformat(),
                        "total_duration_minutes": service.duration_minutes,
                        "items": [{
                            "service_id": service.pk,
                            "service_name": service.name,
                            "start_at": t.isoformat(),
                            "end_at": end_t.isoformat(),
                            "duration_minutes": service.duration_minutes,
                            "employee_id": employee.pk,
                            "employee_name": employee.full_name,
                            "zone_id": zone.pk if zone else None,
                        }],
                    })
                    if max_results and len(results) >= max_results:
                        return results
            t += step
    else:
        while t + timedelta(minutes=total_minutes) <= work_end:
            if t > now:
                plan = _try_schedule_block(services, service_employees, t)
                if plan:
                    local_t = timezone.localtime(t)
                    results.append({
                        "start_at": t.isoformat(),
                        "label": local_t.strftime("%H:%M"),
                        "end_at": (t + timedelta(minutes=total_minutes)).isoformat(),
                        "total_duration_minutes": total_minutes,
                        "items": [
                            {
                                "service_id": item["service"].pk,
                                "service_name": item["service"].name,
                                "start_at": item["start_at"].isoformat(),
                                "end_at": item["end_at"].isoformat(),
                                "duration_minutes": item["service"].duration_minutes,
                                "employee_id": item["employee"].pk,
                                "employee_name": item["employee"].full_name,
                                "zone_id": item["zone"].pk if item["zone"] else None,
                            }
                            for item in plan
                        ],
                    })
                    if max_results and len(results) >= max_results:
                        return results
            t += step
    return results


def _try_schedule_block(services, service_employees, start_at):
    """Try scheduling services consecutively from start_at. Returns plan list or None."""
    current = start_at
    committed = []  # (employee_pk, block_start, block_end)
    plan = []
    for service in services:
        end = current + timedelta(minutes=service.duration_minutes)
        emp = None
        for candidate in service_employees[service.pk]:
            already_used = any(
                eid == candidate.pk and cs < end and ce > current
                for eid, cs, ce in committed
            )
            if already_used:
                continue
            if is_slot_available(candidate, service, None, current, end):
                emp = candidate
                break
        if emp is None:
            return None
        zone = find_available_zone(service, current, end, employee=emp) if service.requires_zone else None
        if service.requires_zone and zone is None:
            return None
        committed.append((emp.pk, current, end))
        plan.append({"service": service, "employee": emp, "zone": zone, "start_at": current, "end_at": end})
        current = end
    return plan
