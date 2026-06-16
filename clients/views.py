from datetime import datetime
from decimal import Decimal
import secrets

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.db.models.deletion import ProtectedError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import can_access_client, get_client_profile, scope_clients_queryset
from auditlog.services import log_event
from bookings.forms import BookingForm
from bookings.client_actions import (
    booking_amount_due,
    booking_paid_amount,
    booking_refundable_until,
    can_client_cancel,
    can_client_reschedule,
    cancel_booking,
    change_booking_service,
    reschedule_booking,
)
from bookings.models import Booking
from bookings.models import BookingPhoto
from bookings.services import calculate_booking_prepayment_amount, create_booking_prepayment, refund_booking_prepayment, refresh_booking_prepayments
from bookings.utils import MOBILE_SLOT_STEP_MINUTES, build_available_slots_for_day, find_available_zone
from employees.models import Employee
from payments.models import Payment as OnlinePayment
from payments.stripe_service import create_checkout_session, create_pending_stripe_payment, get_booking_checkout_amount
from .forms import ClientForm
from .models import Client, ClientRewardRule
from salon.models import Zone
from services_app.models import Service
from .rewards import client_reward_progress
from core.i18n import PUBLIC_LANGUAGE_SESSION_KEY
from core.booking_requests import PUBLIC_PENDING_BOOKING_SESSION_KEY, create_booking_for_client_from_pending
from .translation import CLIENT_LANGUAGE_SESSION_KEY, normalize_client_language


def build_referral_tree(root_client):
    referred_clients = list(
        root_client.referred_clients.all().order_by("first_name", "last_name")
    )

    return {
        "id": root_client.pk,
        "name": root_client.full_name or str(root_client),
        "children": [build_referral_tree(client) for client in referred_clients],
    }


def _build_client_redsys_order_number(booking_id):
    prefix = f"{booking_id % 10000:04d}"
    for _attempt in range(10):
        order_number = f"{prefix}{secrets.token_hex(4).upper()}"
        if not OnlinePayment.objects.filter(order_number=order_number).exists():
            return order_number
    raise ValueError("No se pudo generar un numero de pedido unico.")


def _booking_online_payment_info(booking):
    payments = list(getattr(booking, "_prefetched_objects_cache", {}).get("online_payments", booking.online_payments.all()))
    paid_total = sum((payment.amount for payment in payments if payment.status == OnlinePayment.Statuses.PAID), Decimal("0.00"))
    pending_total = sum((payment.amount for payment in payments if payment.status == OnlinePayment.Statuses.PENDING), Decimal("0.00"))
    total_amount = booking.client_price_snapshot or booking.price_snapshot or Decimal("0.00")
    remaining_amount = max(total_amount - paid_total, Decimal("0.00"))
    latest_payment = payments[0] if payments else None
    return {
        "paid_total": paid_total,
        "pending_total": pending_total,
        "total_amount": total_amount,
        "remaining_amount": remaining_amount,
        "latest_payment": latest_payment,
        "status": latest_payment.status if latest_payment else "",
        "is_paid": remaining_amount <= Decimal("0.00"),
    }


def _attach_online_payment_info(bookings):
    for booking in bookings:
        info = _booking_online_payment_info(booking)
        prepayment = getattr(booking, "prepayment", None)
        booking.online_payment_status = info["status"]
        booking.online_payment_paid_total = info["paid_total"]
        booking.online_payment_pending_total = info["pending_total"]
        booking.online_payment_remaining_amount = info["remaining_amount"]
        booking.prepayment_due_amount = calculate_booking_prepayment_amount(booking)
        booking.online_payment_due_amount = get_booking_checkout_amount(booking)
        booking.amount_due = booking_amount_due(booking)
        booking.refundable_until = booking_refundable_until(booking)
        booking.can_cancel = can_client_cancel(booking)
        booking.can_reschedule = can_client_reschedule(booking)
        booking.online_payment_is_paid = info["is_paid"]
        booking.online_payment_can_pay = (
            not prepayment
            and info["total_amount"] > Decimal("0.00")
            and booking.status not in {Booking.Statuses.CANCELLED, Booking.Statuses.NO_SHOW}
        )
    return bookings


def _is_future_portal_slot(slot):
    current_time = timezone.localtime(timezone.now()).replace(second=0, microsecond=0)
    return timezone.localtime(slot["start_at"]).replace(second=0, microsecond=0) > current_time


def _create_temporary_paid_payment(booking, amount):
    payment = OnlinePayment.objects.create(
        booking=booking,
        amount=amount,
        currency=getattr(settings, "REDSYS_CURRENCY", "978"),
        order_number=_build_client_redsys_order_number(booking.pk),
        method=OnlinePayment.Methods.CARD,
        status=OnlinePayment.Statuses.PAID,
        redsys_response_code="MOCK",
        redsys_authorisation_code="TEMP",
        raw_request={"provider": "mock_client_portal"},
        raw_response={"paid": True, "mode": "temporary_mock"},
        paid_at=timezone.now(),
    )
    create_booking_prepayment(booking, payment)
    return payment


@login_required
def set_client_language(request):
    if not get_client_profile(request.user):
        raise PermissionDenied
    if request.method == "POST":
        language = normalize_client_language(request.POST.get("language"))
        request.session[CLIENT_LANGUAGE_SESSION_KEY] = language
        request.session[PUBLIC_LANGUAGE_SESSION_KEY] = language
        response = redirect(request.POST.get("next") or reverse("clients:portal"))
        response.set_cookie(CLIENT_LANGUAGE_SESSION_KEY, language, max_age=60 * 60 * 24 * 365, samesite="Lax", secure=True)
        response.set_cookie(PUBLIC_LANGUAGE_SESSION_KEY, language, max_age=60 * 60 * 24 * 365, samesite="Lax", secure=True)
        return response
    return redirect("clients:portal")


@login_required
def client_list(request):
    if get_client_profile(request.user):
        return redirect("clients:portal")

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
def client_portal(request):
    client = get_client_profile(request.user)
    if not client:
        raise PermissionDenied

    pending_booking = request.session.pop(PUBLIC_PENDING_BOOKING_SESSION_KEY, None)
    if request.method == "GET" and pending_booking:
        booking, errors = create_booking_for_client_from_pending(client, pending_booking)
        if booking:
            log_event(
                actor=request.user,
                section="booking",
                action="client_portal_pending_create",
                instance=booking,
                message=f"Reserva pendiente creada tras login de cliente: {client.full_name}.",
            )
            messages.success(request, "Solicitud enviada. BRIMOON Studio revisara y confirmara tu cita.")
            return redirect("clients:portal")
        first_error = next((items[0] for items in errors.values() if items), "No se pudo crear la reserva.")
        messages.error(request, first_error)

    if request.method == "POST":
        if request.POST.get("action") == "avatar":
            image = request.FILES.get("avatar")
            if not image:
                messages.error(request, "Selecciona una imagen.")
                return redirect("clients:portal")
            client.avatar = image
            client.save(update_fields=["avatar", "updated_at"])
            log_event(
                actor=request.user,
                section="client",
                action="avatar_update",
                instance=client,
                message=f"Avatar actualizado desde portal cliente: {client.full_name}.",
            )
            messages.success(request, "Avatar actualizado.")
            return redirect("clients:portal")

        data = request.POST.copy()
        data["client"] = str(client.pk)
        data["status"] = Booking.Statuses.PENDING
        data["source"] = Booking.Sources.WEBSITE
        form = BookingForm(
            data,
            allowed_clients=Client.objects.filter(pk=client.pk),
        )
        _configure_client_booking_form(form, client)
        if form.is_valid():
            booking = form.save()
            log_event(
                actor=request.user,
                section="booking",
                action="client_portal_create",
                instance=booking,
                message=f"Solicitud de reserva creada desde portal cliente: {client.full_name}.",
            )
            messages.success(request, "Solicitud enviada. BRIMOON Studio revisara y confirmara tu cita.")
            return redirect("clients:portal")
    else:
        form = None

    return render(
        request,
        "clients/client_portal.html",
        _client_portal_context(request, client, form),
    )


@login_required
def client_portal_slots_api(request):
    client = get_client_profile(request.user)
    if not client:
        raise PermissionDenied

    service_id = request.GET.get("service")
    date_text = request.GET.get("date")
    zone_id = request.GET.get("zone")
    if not service_id or not date_text:
        return JsonResponse({"ok": False, "message": "Selecciona servicio y fecha."}, status=400)

    try:
        service = Service.objects.prefetch_related("allowed_zones", "employees").get(pk=service_id, is_active=True)
        date_value = datetime.strptime(date_text, "%Y-%m-%d").date()
    except (Service.DoesNotExist, ValueError):
        return JsonResponse({"ok": False, "message": "Servicio o fecha no valida."}, status=400)

    zone = None
    if zone_id:
        try:
            zone = Zone.objects.get(pk=zone_id, is_active=True)
        except Zone.DoesNotExist:
            return JsonResponse({"ok": False, "message": "Zona no valida."}, status=400)
        if service.requires_zone and not service.allowed_zones.filter(pk=zone.pk).exists():
            return JsonResponse({"ok": False, "message": "La zona no esta permitida para este servicio."}, status=400)

    slot_map = {}
    employees = (
        Employee.objects
        .filter(is_active=True, services=service)
        .prefetch_related("services")
        .order_by("first_name", "last_name")
    )
    employee_payload = []
    for employee in employees:
        slots, _blocked = build_available_slots_for_day(
            date_obj=date_value,
            employee=employee,
            service=service,
            zone=zone,
            step_minutes=MOBILE_SLOT_STEP_MINUTES,
        )
        slots = [slot for slot in slots if _is_future_portal_slot(slot)]
        first_slot = slots[0] if slots else None
        employee_payload.append(
            {
                "id": employee.pk,
                "name": employee.full_name,
                "next_start_at": timezone.localtime(first_slot["start_at"]).strftime("%Y-%m-%dT%H:%M") if first_slot else "",
                "next_label": timezone.localtime(first_slot["start_at"]).strftime("%H:%M") if first_slot else "",
            }
        )
        for slot in slots:
            start_key = timezone.localtime(slot["start_at"]).strftime("%Y-%m-%dT%H:%M")
            slot_zone = zone
            if service.requires_zone and slot_zone is None:
                slot_zone = find_available_zone(service, slot["start_at"], slot["end_at"])
            item = slot_map.setdefault(
                start_key,
                {
                    "start_at": start_key,
                    "label": timezone.localtime(slot["start_at"]).strftime("%H:%M"),
                    "employees": [],
                },
            )
            item["employees"].append(
                {
                    "id": employee.pk,
                    "name": employee.full_name,
                    "zone": slot_zone.pk if slot_zone else "",
                    "zone_name": slot_zone.name if slot_zone else "",
                }
            )

    return JsonResponse(
        {
            "ok": True,
            "slots": sorted(slot_map.values(), key=lambda item: item["start_at"]),
            "employees": employee_payload,
        }
    )


@login_required
@require_POST
def client_booking_payment(request, pk):
    client = get_client_profile(request.user)
    if not client:
        raise PermissionDenied

    booking = get_object_or_404(
        Booking.objects.select_related("client", "employee", "service").prefetch_related("online_payments"),
        pk=pk,
        client=client,
    )
    if booking.status in {Booking.Statuses.CANCELLED, Booking.Statuses.NO_SHOW}:
        messages.error(request, "Esta reserva no se puede pagar online.")
        return redirect("clients:portal")

    payment_info = _booking_online_payment_info(booking)
    if payment_info["remaining_amount"] <= Decimal("0.00"):
        messages.success(request, "Esta reserva ya esta pagada.")
        return redirect("clients:portal")
    if getattr(booking, "prepayment", None):
        messages.success(request, "Esta reserva ya tiene prepago.")
        return redirect("clients:portal")

    try:
        payment = create_pending_stripe_payment(booking)
        create_checkout_session(payment, request)
    except (ValueError, ValidationError) as exc:
        messages.error(request, str(exc))
        return redirect("clients:portal")
    log_event(
        actor=request.user,
        section="payment",
        action="stripe_checkout_create",
        instance=booking,
        message=f"Stripe Checkout creado desde portal cliente para reserva #{booking.pk}.",
    )
    return redirect(payment.checkout_url)


def _client_booking_queryset(client):
    return (
        Booking.objects
        .select_related("client", "employee", "service", "zone", "prepayment")
        .prefetch_related("online_payments", "online_payments__refunds")
        .filter(client=client)
    )


def _client_booking_detail_context(booking, extra=None):
    _attach_online_payment_info([booking])
    services = Service.objects.filter(is_active=True, employees=booking.employee).order_by("name").distinct()
    context = {
        "booking": booking,
        "services": services,
        "paid_amount": booking_paid_amount(booking),
        "amount_due": booking_amount_due(booking),
        "refundable_until": booking_refundable_until(booking),
        "can_cancel": can_client_cancel(booking),
        "can_reschedule": can_client_reschedule(booking),
        "employees": Employee.objects.filter(is_active=True, services=booking.service).order_by("first_name", "last_name").distinct(),
        "zones": booking.service.allowed_zones.filter(is_active=True).order_by("name") if booking.service.requires_zone else Zone.objects.none(),
    }
    if extra:
        context.update(extra)
    return context


@login_required
def client_booking_detail(request, pk):
    client = get_client_profile(request.user)
    if not client:
        raise PermissionDenied
    booking = get_object_or_404(_client_booking_queryset(client), pk=pk)
    return render(request, "clients/client_booking_detail.html", _client_booking_detail_context(booking))


@login_required
@require_POST
def client_booking_cancel(request, pk):
    client = get_client_profile(request.user)
    if not client:
        raise PermissionDenied
    booking = get_object_or_404(_client_booking_queryset(client), pk=pk)
    try:
        message, _refunds = cancel_booking(booking)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("clients:booking_detail", pk=booking.pk)
    log_event(
        actor=request.user,
        section="booking",
        action="client_cancel",
        instance=booking,
        message=f"Reserva cancelada por cliente desde portal: #{booking.pk}.",
    )
    messages.success(request, message)
    return redirect("clients:booking_detail", pk=booking.pk)


@login_required
@require_POST
def client_booking_reschedule(request, pk):
    client = get_client_profile(request.user)
    if not client:
        raise PermissionDenied
    booking = get_object_or_404(_client_booking_queryset(client), pk=pk)
    try:
        start_at = datetime.strptime(request.POST.get("start_at", ""), "%Y-%m-%dT%H:%M")
        start_at = timezone.make_aware(start_at, timezone.get_default_timezone())
    except ValueError:
        messages.error(request, "Selecciona una fecha y hora válida.")
        return redirect("clients:booking_detail", pk=booking.pk)
    employee = booking.employee
    employee_id = request.POST.get("employee")
    if employee_id:
        employee = get_object_or_404(Employee, pk=employee_id, is_active=True)
    zone = booking.zone
    zone_id = request.POST.get("zone")
    if zone_id:
        zone = get_object_or_404(Zone, pk=zone_id, is_active=True)
    try:
        reschedule_booking(booking, start_at=start_at, employee=employee, zone=zone)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("clients:booking_detail", pk=booking.pk)
    log_event(
        actor=request.user,
        section="booking",
        action="client_reschedule",
        instance=booking,
        message=f"Reserva reprogramada por cliente desde portal: #{booking.pk}.",
    )
    messages.success(request, "La cita se ha cambiado correctamente.")
    return redirect("clients:booking_detail", pk=booking.pk)


@login_required
@require_POST
def client_booking_change_service(request, pk):
    client = get_client_profile(request.user)
    if not client:
        raise PermissionDenied
    booking = get_object_or_404(_client_booking_queryset(client), pk=pk)
    service = get_object_or_404(Service, pk=request.POST.get("service"), is_active=True)
    try:
        result = change_booking_service(booking, service=service, request=request)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("clients:booking_detail", pk=booking.pk)
    payment = result["payment"]
    if payment:
        messages.info(request, "El cambio aumenta el importe. Completa el pago extra para confirmar la diferencia.")
        return redirect(payment.checkout_url)
    if result["manual_refund_required"]:
        messages.warning(request, "El cambio reduce el importe. El salón revisará la diferencia manualmente.")
    else:
        messages.success(request, "Servicio actualizado correctamente.")
    return redirect("clients:booking_detail", pk=booking.pk)


@login_required
@require_POST
def client_booking_prepayment_refund(request, pk):
    client = get_client_profile(request.user)
    if not client:
        raise PermissionDenied

    booking = get_object_or_404(
        Booking.objects.select_related("client", "employee", "service", "prepayment"),
        pk=pk,
        client=client,
    )
    prepayment = getattr(booking, "prepayment", None)
    if not prepayment:
        messages.error(request, "Esta reserva no tiene prepago.")
        return redirect("clients:portal")

    ok, message = refund_booking_prepayment(prepayment)
    if ok:
        log_event(
            actor=request.user,
            section="payment",
            action="prepayment_refund",
            instance=booking,
            message=f"Prepago devuelto desde portal cliente para reserva #{booking.pk}.",
        )
        messages.success(request, message)
    else:
        messages.error(request, message)
    return redirect("clients:portal")


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
            can_manage_credentials=request.user.can_manage_staff,
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
            can_manage_credentials=request.user.can_manage_staff,
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
        can_manage_credentials=request.user.can_manage_staff,
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
            can_manage_credentials=request.user.can_manage_staff,
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
            can_manage_credentials=request.user.can_manage_staff,
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
        .select_related("employee", "service", "zone", "prepayment")
        .prefetch_related("photos", "online_payments")
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
    rewards = client_reward_progress(client)
    available_rewards = sum(reward["available"] for reward in rewards)
    remaining_for_next_reward = min(
        (reward["remaining"] for reward in rewards if reward["remaining"] > 0),
        default=0,
    )

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
        "rewards": rewards,
    }

    return render(request, "clients/client_detail.html", context)


def _client_portal_context(request, client, booking_form=None):
    bookings = (
        Booking.objects
        .select_related("employee", "service", "zone")
        .prefetch_related("photos")
        .filter(client=client)
        .order_by("-start_at")
    )
    done_bookings = bookings.filter(status=Booking.Statuses.DONE)
    total_spent = sum((booking.client_price_snapshot for booking in done_bookings), Decimal("0.00"))
    total_visits = done_bookings.count()
    avg_ticket = total_spent / total_visits if total_visits else Decimal("0.00")
    upcoming_bookings = list(
        bookings.filter(start_at__gte=timezone.now())
        .exclude(status=Booking.Statuses.CANCELLED)
        .order_by("start_at")[:5]
    )
    history = list(bookings[:20])
    _attach_online_payment_info(upcoming_bookings)
    _attach_online_payment_info(history)
    refresh_booking_prepayments(upcoming_bookings)
    refresh_booking_prepayments(history)
    rewards = client_reward_progress(client)
    photo_history = (
        BookingPhoto.objects
        .select_related("booking", "booking__service", "booking__employee", "client")
        .filter(client=client, is_visible_to_client=True)
        .order_by("-created_at")[:24]
    )
    top_services = (
        done_bookings.values("service__name")
        .annotate(total=Count("id"))
        .order_by("-total")[:5]
    )

    if booking_form is None:
        booking_form = BookingForm(
            initial={
                "client": client,
                "status": Booking.Statuses.PENDING,
                "source": Booking.Sources.WEBSITE,
            },
            allowed_clients=Client.objects.filter(pk=client.pk),
        )
    _configure_client_booking_form(booking_form, client)

    return {
        "client": client,
        "booking_form": booking_form,
        "portal_services": [
            {
                "id": service.pk,
                "requires_zone": service.requires_zone,
                "employee_ids": [employee.pk for employee in service.employees.all()],
                "allowed_zone_ids": [zone.pk for zone in service.allowed_zones.all()],
            }
            for service in Service.objects.filter(is_active=True).prefetch_related("employees", "allowed_zones")
        ],
        "portal_zones": [
            {"id": zone.pk, "name": zone.name}
            for zone in Zone.objects.filter(is_active=True).order_by("name")
        ],
        "stats": {
            "total_visits": total_visits,
            "total_spent": total_spent,
            "avg_ticket": avg_ticket,
            "available_rewards": sum(reward["available"] for reward in rewards),
        },
        "upcoming_bookings": upcoming_bookings,
        "bookings": history,
        "photo_history": photo_history,
        "rewards": rewards,
        "top_services": top_services,
    }


def _configure_client_booking_form(form, client):
    form.fields["client"].widget = forms.HiddenInput()
    form.fields["employee"].widget = forms.HiddenInput()
    form.fields["status"].widget = forms.HiddenInput()
    form.fields["source"].widget = forms.HiddenInput()
    form.fields["start_at"].widget = forms.HiddenInput()
    form.fields["end_at"].required = False
    form.fields["end_at"].widget = forms.HiddenInput()
    form.fields["zone"].widget = forms.HiddenInput()
    form.fields["notes"].label = "Comentario"
    form.fields["notes"].widget.attrs["placeholder"] = "Cuéntanos cualquier detalle importante."
    form.fields["reward_rule"].queryset = ClientRewardRule.objects.filter(
        pk__in=[
            reward["id"]
            for reward in client_reward_progress(client)
            if reward["available"] > 0
        ]
    )
    form.fields["reward_rule"].empty_label = "Sin premio"
    form.fields["apply_referral_reward"].widget = forms.HiddenInput()
    
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
