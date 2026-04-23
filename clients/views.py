from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.db.models.deletion import ProtectedError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import can_access_client, scope_clients_queryset
from auditlog.services import log_event
from bookings.models import Booking
from bookings.models import BookingPhoto
from .forms import ClientForm
from .models import Client


def build_referral_tree(root_client):
    referred_clients = list(
        root_client.referred_clients.all().order_by("first_name", "last_name")
    )

    return {
        "id": root_client.pk,
        "name": root_client.full_name or str(root_client),
        "children": [build_referral_tree(client) for client in referred_clients],
    }


@login_required
def client_list(request):
    query = request.GET.get("q", "").strip()

    clients = scope_clients_queryset(Client.objects.all(), request.user)

    if query:
        clients = clients.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(phone__icontains=query) |
            Q(email__icontains=query)
        )

    context = {
        "active_section": "clients",
        "page_title": "Clientes",
        "clients": clients,
        "query": query,
        "clients_count": clients.count(),
    }
    return render(request, "clients/client_list.html", context)


@login_required
def client_create(request):
    referred_by_id = request.GET.get("referred_by")
    initial = {}

    if referred_by_id and request.method == "GET":
        referrer = get_object_or_404(Client, pk=referred_by_id)
        initial["referred_by"] = referrer

    if request.method == "POST":
        form = ClientForm(
            request.POST,
            allowed_referred_by=scope_clients_queryset(
                Client.objects.filter(is_active=True).order_by("first_name", "last_name"),
                request.user,
            ) if not request.user.can_manage_staff else None,
        )
        if form.is_valid():
            client = form.save()
            log_event(
                actor=request.user,
                section="client",
                action="create",
                instance=client,
                message=f"Cliente creado: {client.full_name}.",
            )
            messages.success(request, f"Cliente creado: {client.full_name}")
            return redirect("clients:detail", pk=client.pk)
    else:
        form = ClientForm(
            initial=initial,
            allowed_referred_by=scope_clients_queryset(
                Client.objects.filter(is_active=True).order_by("first_name", "last_name"),
                request.user,
            ) if not request.user.can_manage_staff else None,
        )

    context = {
        "active_section": "clients",
        "page_title": "Nuevo cliente",
        "form": form,
        "is_edit": False,
    }
    return render(request, "clients/client_form.html", context)


@login_required
@require_POST
def client_create_api(request):
    form = ClientForm(
        request.POST,
        allowed_referred_by=scope_clients_queryset(
            Client.objects.filter(is_active=True).order_by("first_name", "last_name"),
            request.user,
        ) if not request.user.can_manage_staff else None,
    )
    if not form.is_valid():
        message = "No se pudo crear el cliente."
        for field_errors in form.errors.values():
            if field_errors:
                message = field_errors[0]
                break
        return JsonResponse(
            {
                "ok": False,
                "message": message,
                "errors": form.errors.get_json_data(),
            },
            status=400,
        )

    client = form.save()
    log_event(
        actor=request.user,
        section="client",
        action="create",
        instance=client,
        message=f"Cliente creado por API: {client.full_name}.",
    )
    return JsonResponse(
        {
            "ok": True,
            "client": {
                "id": client.pk,
                "name": client.full_name or str(client),
                "phone": client.phone or "",
            },
        }
    )


@login_required
def client_update(request, pk):
    client = get_object_or_404(Client, pk=pk)
    if not can_access_client(request.user, client):
        raise PermissionDenied

    if request.method == "POST":
        form = ClientForm(
            request.POST,
            instance=client,
            allowed_referred_by=scope_clients_queryset(
                Client.objects.filter(is_active=True).order_by("first_name", "last_name"),
                request.user,
            ) if not request.user.can_manage_staff else None,
        )
        if form.is_valid():
            client = form.save()
            log_event(
                actor=request.user,
                section="client",
                action="update",
                instance=client,
                message=f"Cliente actualizado: {client.full_name}.",
            )
            messages.success(request, f"Cliente actualizado: {client.full_name}")
            return redirect("clients:detail", pk=client.pk)
    else:
        form = ClientForm(
            instance=client,
            allowed_referred_by=scope_clients_queryset(
                Client.objects.filter(is_active=True).order_by("first_name", "last_name"),
                request.user,
            ) if not request.user.can_manage_staff else None,
        )

    context = {
        "active_section": "clients",
        "page_title": f"Editar cliente: {client.full_name}",
        "form": form,
        "client": client,
        "is_edit": True,
    }
    return render(request, "clients/client_form.html", context)


@login_required
def client_delete(request, pk):
    client = get_object_or_404(Client, pk=pk)
    if not can_access_client(request.user, client):
        raise PermissionDenied
    if not request.user.can_manage_staff:
        raise PermissionDenied

    if request.method == "POST":
        client_name = client.full_name
        try:
            client.delete()
            log_event(
                actor=request.user,
                section="client",
                action="delete",
                message=f"Cliente eliminado: {client_name}.",
            )
            messages.success(request, f"Cliente eliminado: {client_name}")
        except ProtectedError:
            messages.error(
                request,
                "No se puede eliminar este cliente porque tiene reservas u otros datos relacionados."
            )
        return redirect("clients:list")

    return render(
        request,
        "clients/client_confirm_delete.html",
        {
            "active_section": "clients",
            "client": client,
        }
    )


@login_required
def client_detail(request, pk):
    client = get_object_or_404(Client, pk=pk)
    if not can_access_client(request.user, client):
        raise PermissionDenied

    bookings = (
        Booking.objects
        .select_related("employee", "service", "zone")
        .prefetch_related("photos")
        .filter(client=client)
        .order_by("-start_at")
    )
    if not request.user.can_manage_staff:
        bookings = bookings.filter(employee=request.user.employee_profile)
    booking_history = list(bookings[:20])

    done_bookings = bookings.filter(status=Booking.Statuses.DONE)

    total_spent = sum(
        (b.client_price_snapshot for b in done_bookings),
        Decimal("0.00")
    )

    total_visits = done_bookings.count()

    avg_ticket = (
        total_spent / total_visits
        if total_visits else Decimal("0.00")
    )

    last_visit = done_bookings.first()

    next_booking = (
        bookings.filter(start_at__gte=timezone.now())
        .exclude(status=Booking.Statuses.CANCELLED)
        .order_by("start_at")
        .first()
    )

    top_services = (
        done_bookings.values("service__name")
        .annotate(total=Count("id"))
        .order_by("-total")[:5]
    )

    top_employees = (
        done_bookings.values("employee__first_name", "employee__last_name")
        .annotate(total=Count("id"))
        .order_by("-total")[:3]
    )

    referred_clients = scope_clients_queryset(
        client.referred_clients.all().order_by("first_name", "last_name"),
        request.user,
    )
    referred_clients_count = referred_clients.count()

    successful_referrals = referred_clients.filter(
        bookings__status=Booking.Statuses.DONE
    ).distinct()

    successful_referrals_count = successful_referrals.count()
    available_rewards = max((successful_referrals_count // 5) - client.referral_rewards_used, 0)
    remaining_for_next_reward = 5 - (successful_referrals_count % 5) if successful_referrals_count % 5 else 0

    context = {
        "photo_comparisons": [
            {
                "booking": booking,
                "before_photo": next((photo for photo in booking.photos.all() if photo.photo_type == BookingPhoto.PhotoTypes.BEFORE), None),
                "after_photo": next((photo for photo in booking.photos.all() if photo.photo_type == BookingPhoto.PhotoTypes.AFTER), None),
            }
            for booking in booking_history
            if any(photo.photo_type == BookingPhoto.PhotoTypes.BEFORE for photo in booking.photos.all())
            or any(photo.photo_type == BookingPhoto.PhotoTypes.AFTER for photo in booking.photos.all())
        ][:8],
        "active_section": "clients",
        "client": client,
        "bookings": booking_history,
        "photo_history": (
            BookingPhoto.objects
            .select_related("booking", "booking__service", "booking__employee", "client")
            .filter(client=client)
            .order_by("-created_at")[:24]
        ),
        "stats": {
            "total_visits": total_visits,
            "total_spent": total_spent,
            "avg_ticket": avg_ticket,
            "cancelled": bookings.filter(status=Booking.Statuses.CANCELLED).count(),
            "no_show": bookings.filter(status=Booking.Statuses.NO_SHOW).count(),
        },
        "last_visit": last_visit,
        "next_booking": next_booking,
        "top_services": top_services,
        "top_employees": top_employees,
        "referred_clients": referred_clients,
        "referred_clients_count": referred_clients_count,
        "referral_tree": build_referral_tree(client),
        "successful_referrals_count": successful_referrals_count,
        "available_rewards": available_rewards,
        "remaining_for_next_reward": remaining_for_next_reward,
    }

    return render(request, "clients/client_detail.html", context)
    
@login_required
def use_referral_reward(request, pk):
    client = get_object_or_404(Client, pk=pk)

    referred_clients = client.referred_clients.filter(
        bookings__status=Booking.Statuses.DONE
    ).distinct()

    successful_count = referred_clients.count()

    available_rewards = max(
        (successful_count // 5) - client.referral_rewards_used,
        0
    )

    if available_rewards > 0:
        client.referral_rewards_used += 1
        client.save(update_fields=["referral_rewards_used"])
        messages.success(
            request,
            f"Premio aplicado para {client.full_name}"
        )
    else:
        messages.error(request, "No hay premios disponibles.")

    return redirect("clients:detail", pk=client.pk)
