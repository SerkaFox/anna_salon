from datetime import datetime, time
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from bookings.models import Booking
from clients.models import Client
from employees.models import Employee
from services_app.models import Service
from salon.models import Zone


@login_required
def home(request):
    today = timezone.localdate()
    now = timezone.localtime()

    day_start = timezone.make_aware(datetime.combine(today, time.min))
    day_end = timezone.make_aware(datetime.combine(today, time.max))

    today_bookings = Booking.objects.select_related(
        "client", "employee", "service", "zone"
    ).filter(
        start_at__lte=day_end,
        end_at__gte=day_start,
    )

    active_today_bookings = today_bookings.exclude(status=Booking.Statuses.CANCELLED)

    next_booking = (
        active_today_bookings
        .filter(start_at__gte=now)
        .order_by("start_at")
        .first()
    )

    recent_bookings = (
        Booking.objects.select_related("client", "employee", "service", "zone")
        .exclude(status=Booking.Statuses.CANCELLED)
        .order_by("-created_at")[:5]
    )

    client_total_today = sum(
        (booking.client_price_snapshot for booking in active_today_bookings),
        Decimal("0.00"),
    )
    employee_total_today = sum(
        (booking.employee_amount_snapshot for booking in active_today_bookings),
        Decimal("0.00"),
    )
    salon_total_today = sum(
        (booking.salon_amount_snapshot for booking in active_today_bookings),
        Decimal("0.00"),
    )

    context = {
        "active_section": "dashboard",
        "stats": [
            {"label": "Reservas hoy", "value": active_today_bookings.count()},
            {"label": "Pendientes", "value": today_bookings.filter(status=Booking.Statuses.PENDING).count()},
            {"label": "No show", "value": today_bookings.filter(status=Booking.Statuses.NO_SHOW).count()},
            {"label": "Clientes", "value": Client.objects.count()},
            {"label": "Empleados", "value": Employee.objects.filter(is_active=True).count()},
            {"label": "Servicios", "value": Service.objects.filter(is_active=True).count()},
        ],
        "money_stats": [
            {"label": "Cobro clientes hoy", "value": client_total_today},
            {"label": "Pago empleados hoy", "value": employee_total_today},
            {"label": "Ingreso salón hoy", "value": salon_total_today},
        ],
        "next_booking": next_booking,
        "today_bookings": active_today_bookings.order_by("start_at"),
        "recent_bookings": recent_bookings,
        "alerts": {
            "pending_count": today_bookings.filter(status=Booking.Statuses.PENDING).count(),
            "cancelled_count": today_bookings.filter(status=Booking.Statuses.CANCELLED).count(),
            "no_show_count": today_bookings.filter(status=Booking.Statuses.NO_SHOW).count(),
        },
        "zones_count": Zone.objects.filter(is_active=True).count(),
    }
    return render(request, "dashboard/home.html", context)