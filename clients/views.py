from datetime import datetime, timedelta
from decimal import Decimal

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
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
from bookings.utils import MOBILE_SLOT_STEP_MINUTES, PUBLIC_BOOKING_MAX_DAYS_AHEAD, build_available_slots_for_day, find_available_zone
from documents.models import FiscalDocument, FiscalDocumentLine
from employees.models import Employee
from payments.models import Payment as OnlinePayment
from payments.stripe_service import (
    create_checkout_session,
    create_combined_checkout_session,
    create_pending_stripe_payment,
    get_booking_checkout_amount,
    get_booking_deposit_amount,
    get_booking_full_amount,
)
from reviews.forms import ClientReviewForm
from reviews.models import ClientReview, GoogleReview
from notifications.services import (
    notify_booking_cancelled,
    notify_booking_confirmation,
    notify_booking_rescheduled,
    notify_welcome_credentials,
)
from .forms import ClientForm
from .models import Client, ClientRewardRule
from salon.models import Zone
from salon.preferences import get_deposit_percent
from services_app.models import Service
from .rewards import client_reward_progress
from core.i18n import PUBLIC_LANGUAGE_SESSION_KEY
from core.booking_requests import PUBLIC_PENDING_BOOKING_SESSION_KEY, create_booking_for_client_from_pending
from .translation import CLIENT_LANGUAGE_SESSION_KEY, normalize_client_language


def _anonymize_and_delete_client(client):
    """Delete future bookings, wipe personal data, remove the user account.

    Past bookings stay in the DB for statistics, linked to the now-anonymous
    client row (Booking.client is PROTECT so we can't delete the Client row
    while historical bookings exist).
    """
    # 1. Delete all future bookings (frees calendar slots; WA messages cascade)
    Booking.objects.filter(client=client, start_at__gte=timezone.now()).delete()

    # 2. Anonymize client record – clear PII, keep the row for statistics
    if client.avatar:
        client.avatar.delete(save=False)
    client.first_name = "Cuenta"
    client.last_name = "eliminada"
    client.phone = ""
    client.email = ""
    client.notes = ""
    client.avatar = None
    client.is_active = False
    client.referred_by = None
    client.save(update_fields=[
        "first_name", "last_name", "phone", "email",
        "notes", "avatar", "is_active", "referred_by", "updated_at",
    ])

    # 3. Delete user account (Client.user becomes NULL via SET_NULL)
    user = client.user
    if user:
        user.delete()


def build_referral_tree(root_client):
    referred_clients = list(
        root_client.referred_clients.all().order_by("first_name", "last_name")
    )

    return {
        "id": root_client.pk,
        "name": root_client.full_name or str(root_client),
        "children": [build_referral_tree(client) for client in referred_clients],
    }


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
        booking.online_payment_status = info["status"]
        booking.online_payment_paid_total = info["paid_total"]
        booking.online_payment_pending_total = info["pending_total"]
        booking.online_payment_remaining_amount = info["remaining_amount"]
        booking.prepayment_due_amount = calculate_booking_prepayment_amount(booking)
        booking.online_payment_due_amount = get_booking_checkout_amount(booking)
        booking.deposit_payment_amount = get_booking_deposit_amount(booking)
        booking.full_payment_amount = get_booking_full_amount(booking)
        booking.amount_due = booking_amount_due(booking)
        booking.refundable_until = booking_refundable_until(booking)
        booking.can_cancel = can_client_cancel(booking)
        booking.can_reschedule = can_client_reschedule(booking)
        booking.latest_fiscal_document = next(
            (
                document
                for document in booking.fiscal_documents.all()
                if document.status in {FiscalDocument.Statuses.DRAFT, FiscalDocument.Statuses.ISSUED}
            ),
            None,
        )
        booking.online_payment_is_paid = info["is_paid"]
        booking.online_payment_can_pay = (
            info["remaining_amount"] > Decimal("0.00")
            and booking.status not in {Booking.Statuses.CANCELLED, Booking.Statuses.NO_SHOW}
        )
    return bookings


def _is_future_portal_slot(slot):
    current_time = timezone.localtime(timezone.now()).replace(second=0, microsecond=0)
    return timezone.localtime(slot["start_at"]).replace(second=0, microsecond=0) > current_time


def _portal_last_booking_date():
    return timezone.localdate() + timedelta(days=PUBLIC_BOOKING_MAX_DAYS_AHEAD - 1)


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
    client_filter = request.GET.get("filter", "all").strip()
    sort = request.GET.get("sort", "name").strip()

    clients = scope_clients_queryset(
        Client.objects.select_related("user").annotate(
            completed_bookings_count=Count(
                "bookings",
                filter=Q(bookings__status=Booking.Statuses.DONE),
            ),
            completed_bookings_spent=Coalesce(
                Sum(
                    "bookings__client_price_snapshot",
                    filter=Q(bookings__status=Booking.Statuses.DONE),
                ),
                Decimal("0.00"),
            ),
        ),
        request.user,
    )

    if query:
        clients = clients.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(phone__icontains=query) |
            Q(email__icontains=query)
        )

    online_sources = {
        "treatwell_official_channel",
        "uala_official_channel",
        "venue_website",
        "internet",
    }
    if client_filter == "blacklisted":
        clients = clients.filter(is_blacklisted=True)
    elif client_filter == "online":
        clients = clients.filter(
            Q(user__isnull=False) | Q(how_we_met__in=online_sources)
        )

    clients = list(clients)
    for client in clients:
        client.total_orders = client.booking_count + client.completed_bookings_count
        imported_spent = (
            Decimal(client.average_expense_amount_cents)
            * client.booking_count
            / Decimal("100")
        )
        client.total_spent = imported_spent + client.completed_bookings_spent
        client.is_online_client = bool(
            client.user_id or client.how_we_met in online_sources
        )

    if sort == "orders":
        clients.sort(key=lambda item: (-item.total_orders, item.full_name.casefold()))
    elif sort == "spent":
        clients.sort(key=lambda item: (-item.total_spent, item.full_name.casefold()))
    else:
        sort = "name"
        clients.sort(key=lambda item: item.full_name.casefold())

    context = {
        "active_section": "clients",
        "page_title": "Clientes",
        "clients": clients,
        "query": query,
        "clients_count": len(clients),
        "client_filter": client_filter,
        "sort": sort,
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
            try:
                notify_booking_confirmation(booking)
            except Exception:
                pass
            messages.success(request, f"Solicitud enviada. {getattr(settings, 'SALON_NAME', 'BRIMOON Studio')} revisara y confirmara tu cita.")
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

        if client.is_blacklisted:
            messages.error(
                request,
                "Tu cuenta no puede crear reservas online. Contacta con el salón.",
            )
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
            try:
                notify_booking_confirmation(booking)
            except Exception:
                pass
            messages.success(request, f"Solicitud enviada. {getattr(settings, 'SALON_NAME', 'BRIMOON Studio')} revisara y confirmara tu cita.")
            return redirect("clients:portal")
    else:
        form = None

    return render(
        request,
        "clients/client_portal.html",
        _client_portal_context(request, client, form),
    )


@login_required
def client_booking_review(request, pk):
    client = get_client_profile(request.user)
    if not client:
        raise PermissionDenied
    booking = get_object_or_404(
        Booking.objects.select_related("client", "service", "employee"),
        pk=pk,
        client=client,
        status=Booking.Statuses.DONE,
    )
    review = ClientReview.objects.filter(booking=booking, client=client).first()
    if request.method == "POST":
        form = ClientReviewForm(request.POST, instance=review)
        if form.is_valid():
            review = form.save(commit=False)
            review.booking = booking
            review.client = client
            review.save()
            log_event(
                actor=request.user,
                section="review",
                action="client_review",
                instance=booking,
                message=f"Opinion del cliente guardada para la reserva {booking.pk}.",
            )
            messages.success(request, "Gracias. Tu opinion se ha guardado.")
            return redirect("clients:portal")
    else:
        form = ClientReviewForm(instance=review)
    return render(
        request,
        "clients/client_review_form.html",
        {
            "client": client,
            "booking": booking,
            "review": review,
            "form": form,
            "google_review_url": (
                getattr(settings, "GOOGLE_REVIEW_URL", "").strip()
                or GoogleReview.review_url()
            ),
        },
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

    if date_value < timezone.localdate() or date_value > _portal_last_booking_date():
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
    if getattr(settings, "DEMO_MODE", False):
        return redirect("payments:demo_pay", pk=pk)

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

    payment_mode = request.POST.get("payment_mode", "deposit")
    if payment_mode == "deposit" and payment_info["paid_total"] > Decimal("0.00"):
        messages.error(request, "Ya hay un pago registrado para esta reserva. Paga el resto pendiente.")
        return redirect("clients:portal")

    try:
        amount = get_booking_full_amount(booking) if payment_mode == "full" else get_booking_deposit_amount(booking)
        amount = min(amount, payment_info["remaining_amount"])
        payment = create_pending_stripe_payment(
            booking,
            amount=amount,
            reason="booking_full_payment" if payment_mode == "full" else "booking_deposit_payment",
        )
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


@login_required
@require_POST
def client_booking_pay_all(request):
    client = get_client_profile(request.user)
    if not client:
        raise PermissionDenied

    bookings = (
        Booking.objects
        .select_related("client", "employee", "service")
        .prefetch_related("online_payments", "online_payments__refunds")
        .filter(client=client)
        .exclude(status__in=[Booking.Statuses.CANCELLED, Booking.Statuses.NO_SHOW])
    )
    payments = []
    for booking in bookings:
        amount_due = booking_amount_due(booking)
        if amount_due <= Decimal("0.00"):
            continue
        payments.append(create_pending_stripe_payment(booking, amount=amount_due, reason="booking_pay_all"))

    if not payments:
        messages.success(request, "No tienes importes pendientes de pago.")
        return redirect("clients:portal")

    try:
        session = create_combined_checkout_session(payments, request)
    except (ValueError, ValidationError) as exc:
        messages.error(request, str(exc))
        return redirect("clients:portal")

    log_event(
        actor=request.user,
        section="payment",
        action="stripe_checkout_create_all",
        message=f"Stripe Checkout combinado creado para {len(payments)} reservas de {client.full_name}.",
    )
    return redirect(session.url)


def _client_booking_queryset(client):
    return (
        Booking.objects
        .select_related("client", "employee", "service", "zone", "prepayment")
        .prefetch_related("online_payments", "online_payments__refunds")
        .filter(client=client)
    )


def _client_booking_detail_context(booking, extra=None):
    _attach_online_payment_info([booking])
    fiscal_document = booking.fiscal_documents.filter(status__in=[
        FiscalDocument.Statuses.DRAFT,
        FiscalDocument.Statuses.ISSUED,
    ]).order_by("-document_type", "-id").first()
    services = Service.objects.filter(is_active=True, employees=booking.employee).order_by("name").distinct()
    context = {
        "booking": booking,
        "fiscal_document": fiscal_document,
        "services": services,
        "paid_amount": booking_paid_amount(booking),
        "amount_due": booking_amount_due(booking),
        "deposit_amount": min(get_booking_deposit_amount(booking), booking_amount_due(booking)),
        "full_amount": booking_amount_due(booking),
        "refundable_until": booking_refundable_until(booking),
        "can_cancel": can_client_cancel(booking),
        "can_reschedule": can_client_reschedule(booking),
        "employees": Employee.objects.filter(is_active=True, services=booking.service).order_by("first_name", "last_name").distinct(),
        "zones": booking.service.allowed_zones.filter(is_active=True).order_by("name") if booking.service.requires_zone else Zone.objects.none(),
    }
    if extra:
        context.update(extra)
    return context


def _ensure_client_document_line(document):
    if document.lines.exists():
        return
    booking = document.booking
    paid_amount = booking_paid_amount(booking)
    total_price = booking.client_price_snapshot or booking.price_snapshot or Decimal("0.00")
    description = str(booking.service)
    if paid_amount < total_price:
        description = f"{description} (pago a cuenta)"
    FiscalDocumentLine.objects.create(
        fiscal_document=document,
        service=booking.service,
        description=description,
        quantity=Decimal("1.00"),
        unit_amount=paid_amount,
        sort_order=0,
    )
    document.save(update_fields=["subtotal_amount", "tax_amount", "total_amount", "updated_at"])


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
    try:
        notify_booking_cancelled(booking)
    except Exception:
        pass
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
    try:
        notify_booking_rescheduled(booking)
    except Exception:
        pass
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
        result = change_booking_service(booking, service=service)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("clients:booking_detail", pk=booking.pk)
    if result["manual_refund_required"]:
        messages.warning(request, "El cambio reduce el importe. El salón revisará la diferencia manualmente.")
    elif result["extra_due"] > Decimal("0.00"):
        messages.success(
            request,
            f"Servicio actualizado. Nuevo importe: {result['new_total']} €. Pendiente de pago: {result['extra_due']} €. Puedes pagarlo cuando quieras desde tu cuenta.",
        )
    else:
        messages.success(request, "Servicio actualizado correctamente.")
    return redirect("clients:booking_detail", pk=booking.pk)


@login_required
@require_POST
def client_booking_document(request, pk):
    client = get_client_profile(request.user)
    if not client:
        raise PermissionDenied
    booking = get_object_or_404(_client_booking_queryset(client), pk=pk)
    if booking_paid_amount(booking) <= Decimal("0.00"):
        messages.error(request, "Todavía no hay ningún pago registrado para esta reserva.")
        return redirect("clients:booking_detail", pk=booking.pk)
    document_type = request.POST.get("document_type") or FiscalDocument.DocumentTypes.RECEIPT
    if document_type not in {FiscalDocument.DocumentTypes.RECEIPT, FiscalDocument.DocumentTypes.INVOICE}:
        document_type = FiscalDocument.DocumentTypes.RECEIPT
    if document_type == FiscalDocument.DocumentTypes.INVOICE:
        fiscal_id = (request.POST.get("fiscal_id") or "").strip()
        fiscal_address = (request.POST.get("fiscal_address") or "").strip()
        fiscal_city = (request.POST.get("fiscal_city") or "").strip()
        fiscal_postcode = (request.POST.get("fiscal_postcode") or "").strip()
        if fiscal_id or fiscal_address:
            client.fiscal_id = fiscal_id or client.fiscal_id
            client.fiscal_address = fiscal_address or client.fiscal_address
            client.fiscal_city = fiscal_city or client.fiscal_city
            client.fiscal_postcode = fiscal_postcode or client.fiscal_postcode
            client.save(update_fields=["fiscal_id", "fiscal_address", "fiscal_city", "fiscal_postcode", "updated_at"])
        if not client.fiscal_id or not client.fiscal_address:
            messages.error(request, "Para una factura completa añade NIE/NIF y dirección fiscal en tu perfil.")
            return redirect("clients:booking_detail", pk=booking.pk)
    document, _created = FiscalDocument.objects.get_or_create(
        booking=booking,
        document_type=document_type,
        status__in=[FiscalDocument.Statuses.DRAFT, FiscalDocument.Statuses.ISSUED],
        defaults={
            "status": FiscalDocument.Statuses.ISSUED,
            "issue_date": timezone.localdate(),
        },
    )
    _ensure_client_document_line(document)
    messages.success(request, "Documento preparado. Puedes imprimirlo o guardarlo como PDF.")
    return redirect("documents:print", pk=document.pk)


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
        _anonymize_and_delete_client(client)
        log_event(
            actor=request.user,
            section="client",
            action="delete",
            message=f"Cliente eliminado y anonimizado: {client_name}.",
        )
        messages.success(request, f"Cliente eliminado: {client_name}")
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

    from dashboard.views import _build_client_rating
    client_rating = _build_client_rating(client, timezone.now())

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
        "client_rating": client_rating,
    }

    return render(request, "clients/client_detail.html", context)


def _client_portal_context(request, client, booking_form=None):
    bookings = (
        Booking.objects
        .select_related("employee", "service", "zone")
        .prefetch_related("photos", "fiscal_documents")
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
    total_amount_due = sum(
        (booking.amount_due for booking in upcoming_bookings if booking.online_payment_can_pay),
        Decimal("0.00"),
    )
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
    client_reviews = list(
        ClientReview.objects.select_related("booking", "booking__service")
        .filter(client=client)
        .order_by("-created_at")
    )
    reviewable_bookings = list(
        done_bookings.filter(client_review__isnull=True)
        .select_related("service", "employee")
        .order_by("-completed_at", "-end_at")[:5]
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
        "total_amount_due": total_amount_due,
        "bookings": history,
        "photo_history": photo_history,
        "rewards": rewards,
        "top_services": top_services,
        "client_reviews": client_reviews,
        "reviewable_bookings": reviewable_bookings,
        "google_review_url": (
            getattr(settings, "GOOGLE_REVIEW_URL", "").strip()
            or GoogleReview.review_url()
        ),
        "booking_last_date": _portal_last_booking_date().isoformat(),
        "booking_search_days": PUBLIC_BOOKING_MAX_DAYS_AHEAD,
        "deposit_percent": get_deposit_percent(),
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


@login_required
def client_delete_own_account(request):
    client = get_client_profile(request.user)
    if not client:
        raise PermissionDenied

    if request.method == "POST":
        password = request.POST.get("password") or ""
        if not request.user.check_password(password):
            messages.error(request, "Contrasena incorrecta.")
            return render(request, "clients/client_delete_account.html", {"active_section": "profile"})

        user = request.user
        client_name = client.full_name
        log_event(
            actor=user,
            section="client",
            action="self_delete",
            instance=client,
            message=f"Cuenta eliminada por el propio cliente: {client_name}.",
        )
        logout(request)
        _anonymize_and_delete_client(client)
        messages.success(request, f"Tu cuenta ha sido eliminada. Gracias por confiar en {getattr(settings, 'SALON_NAME', 'BRIMOON Studio')}.")
        return redirect("home")

    return render(request, "clients/client_delete_account.html", {"active_section": "profile"})


@login_required
@require_POST
def notification_unsubscribe(request):
    client = get_client_profile(request.user)
    if not client:
        raise PermissionDenied

    channel = request.POST.get("channel")
    if channel not in ("whatsapp", "email"):
        messages.error(request, "Canal no válido.")
        return redirect("clients:portal")

    # Never let the client unsubscribe from the last active channel
    active_channels = sum([
        bool(client.phone and client.notify_whatsapp),
        bool(client.email and client.notify_email),
    ])
    if active_channels <= 1:
        messages.error(request, "Debes mantener al menos un canal de notificaciones activo.")
        return redirect("clients:portal")

    if channel == "whatsapp":
        client.notify_whatsapp = False
        client.save(update_fields=["notify_whatsapp", "updated_at"])
        messages.success(request, "Te has dado de baja de las notificaciones por WhatsApp.")
    else:
        client.notify_email = False
        client.save(update_fields=["notify_email", "updated_at"])
        messages.success(request, "Te has dado de baja de las notificaciones por email.")

    return redirect("clients:portal")

    return render(request, "clients/client_delete_account.html", {"active_section": "profile"})
