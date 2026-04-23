from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils import timezone

from bookings.models import Booking
from clients.models import Client
from employees.models import Employee
from services_app.models import Service
from salon.models import Zone


def _build_client_rating(client, reference_time):
    bookings = sorted(client.bookings.all(), key=lambda booking: booking.start_at, reverse=True)
    done_bookings = [booking for booking in bookings if booking.status == Booking.Statuses.DONE]
    no_show_bookings = [booking for booking in bookings if booking.status == Booking.Statuses.NO_SHOW]
    cancelled_bookings = [booking for booking in bookings if booking.status == Booking.Statuses.CANCELLED]
    recent_threshold = reference_time - timedelta(days=90)
    recent_done_bookings = [booking for booking in done_bookings if booking.start_at >= recent_threshold]

    successful_referrals = 0
    for referred_client in client.referred_clients.all():
        referred_bookings = referred_client.bookings.all()
        if any(booking.status == Booking.Statuses.DONE for booking in referred_bookings):
            successful_referrals += 1

    score = (
        len(done_bookings) * 10
        + len(recent_done_bookings) * 4
        + successful_referrals * 18
        - len(no_show_bookings) * 45
        - len(cancelled_bookings) * 8
    )

    strong_history = len(done_bookings) >= max(4, len(no_show_bookings) * 4)
    repeated_no_show_pattern = len(no_show_bookings) >= 2 and len(done_bookings) < len(no_show_bookings) * 3

    if score >= 120 and not no_show_bookings:
        rating_label = "VIP"
    elif score >= 75 and (strong_history or not no_show_bookings):
        rating_label = "Fiel"
    elif repeated_no_show_pattern or (no_show_bookings and score < 30 and not strong_history):
        rating_label = "Riesgoso"
    elif score < 0:
        rating_label = "Inestable"
    elif score >= 30:
        rating_label = "Activo"
    else:
        rating_label = "Nuevo"

    issue_reasons = []
    for booking in no_show_bookings[:3]:
        issue_reasons.append(
            {
                "label": "No asistió a la cita",
                "booking": booking,
            }
        )
    for booking in cancelled_bookings[:2]:
        issue_reasons.append(
            {
                "label": "Reserva cancelada",
                "booking": booking,
            }
        )

    last_done_booking = done_bookings[0] if done_bookings else None
    explanation_parts = []

    if len(done_bookings) >= 8:
        explanation_parts.append("muchas visitas hechas")
    elif len(done_bookings) >= 3:
        explanation_parts.append("buen historial de visitas")
    elif len(done_bookings) > 0:
        explanation_parts.append("ya tiene visitas completadas")
    else:
        explanation_parts.append("todavía sin historial sólido")

    if successful_referrals:
        explanation_parts.append(f"{successful_referrals} referido(s) efectivo(s)")

    if recent_done_bookings:
        explanation_parts.append("actividad reciente")

    if no_show_bookings:
        if strong_history:
            explanation_parts.append(f"{len(no_show_bookings)} no show, compensado por buen historial")
        else:
            explanation_parts.append(f"{len(no_show_bookings)} no show")

    if cancelled_bookings:
        explanation_parts.append(f"{len(cancelled_bookings)} cancelación(es)")

    explanation = ", ".join(explanation_parts[:4])

    return {
        "client": client,
        "score": score,
        "rating_label": rating_label,
        "rating_explanation": explanation,
        "done_count": len(done_bookings),
        "recent_done_count": len(recent_done_bookings),
        "successful_referrals": successful_referrals,
        "no_show_count": len(no_show_bookings),
        "cancelled_count": len(cancelled_bookings),
        "issue_reasons": issue_reasons,
        "last_done_booking": last_done_booking,
    }


def _get_client_ranking_context(reference_time):
    clients_for_rating = (
        Client.objects
        .filter(is_active=True)
        .prefetch_related(
            "bookings",
            "referred_clients__bookings",
        )
    )
    client_ratings = [
        _build_client_rating(client, reference_time)
        for client in clients_for_rating
        if client.bookings.all() or client.referred_clients.all()
    ]
    client_rankings = sorted(
        client_ratings,
        key=lambda row: (row["score"], row["done_count"], row["successful_referrals"]),
        reverse=True,
    )[:12]
    return {
        "client_rankings": client_rankings,
    }


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

    overdue_bookings = (
        Booking.objects.select_related("client", "employee", "service", "zone")
        .filter(end_at__lt=now)
        .exclude(
            status__in=[
                Booking.Statuses.DONE,
                Booking.Statuses.CANCELLED,
                Booking.Statuses.NO_SHOW,
            ]
        )
        .order_by("end_at")
    )

    ranking_context = _get_client_ranking_context(now)

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
        "overdue_bookings": overdue_bookings[:8],
        "overdue_bookings_count": overdue_bookings.count(),
        **ranking_context,
        "alerts": {
            "pending_count": today_bookings.filter(status=Booking.Statuses.PENDING).count(),
            "cancelled_count": today_bookings.filter(status=Booking.Statuses.CANCELLED).count(),
            "no_show_count": today_bookings.filter(status=Booking.Statuses.NO_SHOW).count(),
            "overdue_count": overdue_bookings.count(),
        },
        "zones_count": Zone.objects.filter(is_active=True).count(),
    }
    return render(request, "dashboard/home.html", context)


@login_required
def client_rankings_partial(request):
    ranking_context = _get_client_ranking_context(timezone.localtime())
    html = render_to_string(
        "dashboard/_client_rankings.html",
        ranking_context,
        request=request,
    )
    return JsonResponse({"ok": True, "html": html})
