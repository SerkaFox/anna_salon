from datetime import timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from bookings.forms import BookingForm
from bookings.models import Booking
from bookings.utils import find_available_zone
from clients.models import Client
from employees.models import Employee
from salon.models import Zone
from services_app.models import Service


PUBLIC_PENDING_BOOKING_SESSION_KEY = "public_pending_booking"


def pending_booking_from_post(post):
    return {
        "service": str(post.get("service") or ""),
        "employee": str(post.get("employee") or ""),
        "zone": str(post.get("zone") or ""),
        "start_at": str(post.get("start_at") or ""),
    }


def create_booking_for_client_from_pending(client, pending):
    errors = {}
    service = employee = zone = start_at = None

    try:
        service = Service.objects.prefetch_related("allowed_zones", "employees").get(pk=pending.get("service"), is_active=True)
    except (Service.DoesNotExist, ValueError, TypeError):
        errors["service"] = ["Selecciona un servicio valido."]

    if service:
        try:
            employee = Employee.objects.get(pk=pending.get("employee"), is_active=True, services=service)
        except (Employee.DoesNotExist, ValueError, TypeError):
            errors["employee"] = ["Selecciona una especialista disponible."]

    if pending.get("zone"):
        try:
            zone = Zone.objects.get(pk=pending.get("zone"), is_active=True)
        except (Zone.DoesNotExist, ValueError, TypeError):
            errors["zone"] = ["Zona no valida."]

    start_at = parse_datetime(pending.get("start_at") or "")
    if not start_at:
        errors["start_at"] = ["Selecciona una hora disponible."]
    else:
        if timezone.is_naive(start_at):
            start_at = timezone.make_aware(start_at)
        start_at = timezone.localtime(start_at).replace(second=0, microsecond=0)
        if start_at < timezone.localtime(timezone.now()).replace(second=0, microsecond=0):
            errors["start_at"] = ["Selecciona una hora futura."]

    if errors:
        return None, errors

    end_at = start_at + timedelta(minutes=service.duration_minutes)
    if service.requires_zone and zone is None:
        zone = find_available_zone(service, start_at, end_at)

    form = BookingForm(
        {
            "client": client.pk,
            "employee": employee.pk,
            "service": service.pk,
            "zone": zone.pk if zone else "",
            "start_at": timezone.localtime(start_at).strftime("%Y-%m-%dT%H:%M"),
            "end_at": timezone.localtime(end_at).strftime("%Y-%m-%dT%H:%M"),
            "status": Booking.Statuses.PENDING,
            "source": Booking.Sources.WEBSITE,
            "notes": "Reserva creada desde la web publica.",
        },
        allowed_clients=Client.objects.filter(pk=client.pk),
    )
    if not form.is_valid():
        return None, {field: [str(item) for item in field_errors] for field, field_errors in form.errors.items()}
    return form.save(), {}
