import json
import secrets
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth import login
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from accounts.models import User
from bookings.forms import BookingForm
from bookings.models import Booking, BookingWaitlistEntry
from bookings.services import calculate_booking_prepayment_amount, create_booking_prepayment
from bookings.utils import MOBILE_SLOT_STEP_MINUTES, build_available_slots_for_day, find_available_zone
from clients.models import Client
from clients.translation import CLIENT_LANGUAGE_SESSION_KEY
from accounts.permissions import get_client_profile
from employees.models import Employee
from payments.models import Payment as OnlinePayment
from gallery.models import InstagramPost
from salon.models import Zone
from services_app.models import Service

from .i18n import (
    ARTICLE_TRANSLATIONS,
    PUBLIC_LANGUAGE_SESSION_KEY,
    PUBLIC_LANGUAGES,
    SERVICE_TRANSLATIONS,
    detect_public_language,
    localize_items,
    normalize_public_language,
    public_texts,
)
from .booking_requests import (
    PUBLIC_PENDING_BOOKING_SESSION_KEY,
    create_booking_for_client_from_pending,
    pending_booking_from_post,
)


SITE_NAME = "BRIMOON Studio"
SITE_DOMAIN = settings.PUBLIC_BASE_URL.rstrip("/")


class PublicBookingError(Exception):
    def __init__(self, errors):
        self.errors = errors


SERVICES = [
    {
        "slug": "cejas-definicion-depilacion-lifting",
        "title": "Definición, depilación y lifting de cejas",
        "short": "Diseño de cejas, depilación precisa y lifting para realzar tu mirada.",
        "image": "Definición, depilación y lifting de cejas.png",
        "meta": "Diseño de cejas, depilación y lifting en BRIMOON Studio: cuidado profesional para realzar la expresión natural de tu mirada.",
        "intro": "Un servicio pensado para ordenar, equilibrar y elevar la expresión del rostro sin perder naturalidad.",
        "benefits": [
            "Diseño adaptado a la forma del rostro y a la densidad natural de la ceja.",
            "Depilación precisa para limpiar el contorno sin endurecer la mirada.",
            "Lifting para aportar dirección, volumen visual y un acabado más pulido.",
        ],
        "tips": [
            "Evita retocar la ceja en casa antes de la cita para poder trabajar con más margen.",
            "No apliques aceites ni productos muy grasos el mismo día del lifting.",
            "Respeta las primeras horas de cuidado posterior para mantener el resultado más tiempo.",
        ],
    },
    {
        "slug": "depilacion-facial",
        "title": "Depilación facial",
        "short": "Tratamientos delicados para una piel más limpia, suave y cuidada.",
        "image": "Depilación facial.png",
        "meta": "Depilación facial delicada en BRIMOON Studio: cuidado de la piel, precisión y acabado suave con cita previa.",
        "intro": "Trabajamos la depilación facial con precisión y cuidado para conseguir una piel más limpia sin perder confort.",
        "benefits": [
            "Acabado limpio en zonas visibles del rostro.",
            "Técnica delicada para cuidar pieles sensibles siempre que sea posible.",
            "Resultado ordenado que mejora la sensación de suavidad y frescura.",
        ],
        "tips": [
            "No exfolies la zona el día anterior si tu piel suele reaccionar.",
            "Evita sol directo y calor intenso justo después del servicio.",
            "Mantén la hidratación suave y evita activos fuertes durante las primeras horas.",
        ],
    },
    {
        "slug": "manicura-extensiones-tratamientos",
        "title": "Manicura, extensiones y tratamientos",
        "short": "Manicuras elegantes, extensiones y tratamientos para unas manos impecables.",
        "image": "Manicura, extensiones y tratamientos.png",
        "meta": "Manicura, extensiones y tratamientos de uñas en BRIMOON Studio: acabado elegante, cuidado de manos y diseño personalizado.",
        "intro": "La manicura en Brimoon combina estética, preparación cuidada y detalles que elevan el acabado final.",
        "benefits": [
            "Preparación de la uña y cutícula para un acabado más limpio.",
            "Extensiones y tratamientos orientados a mejorar forma, resistencia y estilo.",
            "Diseños elegantes que se adaptan a tu día a día o a una ocasión especial.",
        ],
        "tips": [
            "Hidrata las manos a diario, pero evita crema justo antes de la cita.",
            "Usa aceite de cutícula para mantener un aspecto más cuidado entre visitas.",
            "No arranques producto si notas levantamiento: agenda una revisión para proteger la uña natural.",
        ],
    },
    {
        "slug": "pedicuras-tratamientos-pies",
        "title": "Pedicuras y tratamientos",
        "short": "Cuidado completo de pies, pedicura estética y tratamientos para bienestar y belleza.",
        "image": "Pedicuras y tratamientos Pies.png",
        "meta": "Pedicuras y tratamientos de pies en BRIMOON Studio: cuidado estético, bienestar y acabado elegante con cita previa.",
        "intro": "Un cuidado completo para que los pies se vean más bonitos y se sientan más descansados.",
        "benefits": [
            "Cuidado estético de uñas y piel para un aspecto más pulido.",
            "Tratamientos orientados al bienestar y a la sensación de ligereza.",
            "Acabados limpios para sandalias, eventos o mantenimiento regular.",
        ],
        "tips": [
            "Llega con calzado cómodo si vas a elegir esmaltado tradicional.",
            "Hidrata los pies por la noche para mantener la piel más flexible.",
            "Agenda mantenimiento regular si buscas un resultado cuidado todo el año.",
        ],
    },
    {
        "slug": "pestanas-tinte-extensiones-lifting",
        "title": "Tinte, extensiones y lifting de pestañas",
        "short": "Color, volumen y curvatura para una mirada más intensa y definida.",
        "image": "Tinte, extensiones y lifting de pestañas..png",
        "meta": "Tinte, extensiones y lifting de pestañas en BRIMOON Studio: mirada más definida, volumen y curvatura con acabado premium.",
        "intro": "Servicios para intensificar la mirada con un resultado elegante, definido y adaptado a tus facciones.",
        "benefits": [
            "Tinte para aportar profundidad visual a pestañas claras o poco marcadas.",
            "Lifting para elevar la curvatura natural sin efecto pesado.",
            "Extensiones para conseguir más volumen y presencia según el estilo buscado.",
        ],
        "tips": [
            "Acude sin máscara de pestañas para que el trabajo sea más limpio.",
            "Evita vapor, aceites y agua directa durante las primeras horas si se indica en cabina.",
            "Cepilla las pestañas con suavidad para mantenerlas ordenadas entre visitas.",
        ],
    },
]

ARTICLES = [
    {
        "slug": "como-cuidar-cejas-despues-lifting",
        "title": "Cómo cuidar tus cejas después de un lifting",
        "category": "Cejas",
        "summary": "Pequeños hábitos para conservar la forma, el brillo y la dirección del lifting de cejas.",
        "meta": "Consejos para cuidar las cejas después de un lifting: primeras horas, hidratación y mantenimiento profesional.",
        "image": "Brows.png",
        "sections": [
            ("Las primeras horas importan", "El resultado se estabiliza mejor cuando se evita humedad intensa, vapor y productos grasos durante el tiempo recomendado en cabina."),
            ("Peina con suavidad", "Un cepillado ligero ayuda a mantener la dirección sin forzar el pelo. La clave es ordenar, no arrastrar."),
            ("Mantenimiento inteligente", "Agenda retoques cuando notes que la dirección empieza a perder fuerza. Forzar la ceja en casa suele empeorar el acabado."),
        ],
    },
    {
        "slug": "manicura-elegante-que-dura",
        "title": "Manicura elegante: hábitos para que dure más",
        "category": "Manicura",
        "summary": "Cuidado diario, aceite de cutícula y gestos sencillos para proteger el acabado.",
        "meta": "Consejos de manicura elegante y duradera: cuidado de manos, cutículas y mantenimiento entre citas.",
        "image": "Manicure.png",
        "sections": [
            ("Protege el acabado", "Usa guantes para tareas con agua o productos de limpieza. Es el gesto más simple y uno de los más eficaces."),
            ("Cuida la cutícula", "El aceite aplicado con constancia mejora el aspecto de la manicura y ayuda a que el contorno se vea más limpio."),
            ("No retires producto en casa", "Si aparece levantamiento, lo mejor es revisar la uña en cabina para evitar dañar la base natural."),
        ],
    },
    {
        "slug": "depilacion-facial-piel-sensible",
        "title": "Depilación facial y piel sensible: qué tener en cuenta",
        "category": "Piel",
        "summary": "Preparación y cuidados posteriores para que la piel se sienta más calmada.",
        "meta": "Depilación facial para piel sensible: recomendaciones antes y después para cuidar la piel.",
        "image": "Skin.png",
        "sections": [
            ("Prepara la piel", "Evita exfoliaciones fuertes antes de la cita si sueles tener rojez o sensibilidad."),
            ("Menos es más", "Después del servicio, elige hidratación suave y evita activos intensos durante las primeras horas."),
            ("Observa tu ritmo", "Cada piel responde diferente. Comentar tus reacciones anteriores ayuda a ajustar mejor el servicio."),
        ],
    },
    {
        "slug": "mirada-natural-pestanas-cejas",
        "title": "Mirada natural: combinar cejas y pestañas sin exceso",
        "category": "Mirada",
        "summary": "Ideas para realzar la mirada manteniendo un acabado fino y sofisticado.",
        "meta": "Cómo combinar cejas y pestañas para una mirada natural, elegante y definida en BRIMOON Studio.",
        "image": "Eyes.png",
        "sections": [
            ("Equilibrio antes que intensidad", "Una mirada elegante no siempre necesita más volumen. A veces basta con dirección, color y proporción."),
            ("Respeta tus facciones", "El diseño debe acompañar la forma natural del ojo y del rostro para que el resultado se vea propio."),
            ("Planifica por eventos", "Si tienes una ocasión especial, reserva con margen para ajustar el resultado sin prisas."),
        ],
    },
]


def _absolute_url(request, path):
    return f"{SITE_DOMAIN}{path}"


def _find_by_slug(items, slug):
    for item in items:
        if item["slug"] == slug:
            return item
    raise Http404("Página no encontrada")


def _service_schema(request, service):
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "Service",
        "name": service["title"],
        "description": service["meta"],
        "provider": {"@type": "BeautySalon", "name": SITE_NAME},
        "areaServed": "Bilbao",
        "url": _absolute_url(request, reverse("service_detail", args=[service["slug"]])),
    }, ensure_ascii=False)


def _article_schema(request, article):
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": article["title"],
        "description": article["meta"],
        "author": {"@type": "Organization", "name": SITE_NAME},
        "publisher": {"@type": "Organization", "name": SITE_NAME},
        "mainEntityOfPage": _absolute_url(request, reverse("article_detail", args=[article["slug"]])),
    }, ensure_ascii=False)


def _localized_context(request):
    language = detect_public_language(request)
    services = localize_items(SERVICES, SERVICE_TRANSLATIONS, language)
    articles = localize_items(ARTICLES, ARTICLE_TRANSLATIONS, language)
    return language, public_texts(language), services, articles


def _base_context(request, canonical_path):
    language, t, services, articles = _localized_context(request)
    return {
        "public_language": language,
        "public_languages": PUBLIC_LANGUAGES,
        "t": t,
        "services": services,
        "articles": articles,
        "canonical_url": _absolute_url(request, canonical_path),
    }


def _format_public_datetime(value):
    return timezone.localtime(value).isoformat()


def _is_future_public_slot(slot):
    current_time = timezone.localtime(timezone.now()).replace(second=0, microsecond=0)
    return timezone.localtime(slot["start_at"]).replace(second=0, microsecond=0) > current_time


def _public_booking_services():
    return Service.objects.filter(is_active=True).order_by("name")


def _generate_client_username(name):
    first_word = (name or "").strip().split(None, 1)[0] if (name or "").strip() else "cliente"
    base = slugify(first_word)[:24] or "cliente"
    if not User.objects.filter(username=base).exists():
        return base
    for suffix in range(2, 10000):
        username = f"{base}{suffix}"
        if not User.objects.filter(username=username).exists():
            return username
    return f"{base}{secrets.token_hex(3)}"


def _generate_public_payment_order_number(booking_id):
    prefix = f"{booking_id % 10000:04d}"
    for _attempt in range(10):
        order_number = f"{prefix}{secrets.token_hex(4).upper()}"
        if not OnlinePayment.objects.filter(order_number=order_number).exists():
            return order_number
    raise PublicBookingError({"__all__": ["No se pudo generar el pago de la reserva."]})


def _public_wants_json(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")


def _public_booking_error_response(request, values, errors):
    if _public_wants_json(request):
        return JsonResponse({"ok": False, "errors": errors}, status=400)
    return render(request, "core/public_booking.html", _public_booking_context(request, values, errors), status=400)


def _public_booking_context(request, values=None, errors=None):
    context = _base_context(request, reverse("public_booking"))
    context.update(
        {
            "booking_services": _public_booking_services(),
            "booking_values": values or {},
            "booking_errors": errors or {},
            "booking_non_field_errors": (errors or {}).get("__all__", []),
            "today": timezone.localdate().isoformat(),
            "slot_endpoint": reverse("public_booking_slots"),
            "waitlist_employees": [
                {
                    "id": employee.pk,
                    "name": employee.full_name,
                    "service_ids": [service.pk for service in employee.services.all()],
                }
                for employee in Employee.objects.filter(is_active=True).prefetch_related("services").order_by("first_name", "last_name")
            ],
        }
    )
    return context


def _slot_matches(selected_start, slots):
    selected_local = timezone.localtime(selected_start).replace(second=0, microsecond=0)
    for slot in slots:
        slot_local = timezone.localtime(slot["start_at"]).replace(second=0, microsecond=0)
        if slot_local == selected_local:
            return slot
    return None


def _create_public_booking(values):
    name = values["name"].strip()
    parts = name.split(None, 1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else ""
    phone = values.get("phone", "").strip()
    email = values.get("email", "").strip()

    with transaction.atomic():
        user = User(
            username=_generate_client_username(name),
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            role=User.ROLE_CLIENT,
        )
        user.set_password(values["password"])
        user.save()
        client = Client.objects.create(
            user=user,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            email=email,
            is_active=True,
        )
        form = BookingForm(
            data={
                "client": client.pk,
                "employee": values["employee"].pk,
                "service": values["service"].pk,
                "zone": values["zone"].pk if values.get("zone") else "",
                "start_at": timezone.localtime(values["start_at"]).strftime("%Y-%m-%dT%H:%M"),
                "end_at": timezone.localtime(values["end_at"]).strftime("%Y-%m-%dT%H:%M"),
                "status": Booking.Statuses.CONFIRMED,
                "source": Booking.Sources.WEBSITE,
                "notes": "Reserva creada desde la web publica. Pago temporal confirmado.",
            },
            allowed_clients=Client.objects.filter(pk=client.pk),
        )
        if not form.is_valid():
            raise PublicBookingError({field: [str(item) for item in errors] for field, errors in form.errors.items()})
        booking = form.save()
        amount = calculate_booking_prepayment_amount(booking)
        if amount:
            payment = OnlinePayment.objects.create(
                booking=booking,
                amount=amount,
                currency=getattr(settings, "REDSYS_CURRENCY", "978"),
                order_number=_generate_public_payment_order_number(booking.pk),
                method=OnlinePayment.Methods.CARD,
                status=OnlinePayment.Statuses.PAID,
                redsys_response_code="MOCK",
                redsys_authorisation_code="TEMP",
                raw_request={"provider": "mock_public_checkout"},
                raw_response={"paid": True, "mode": "temporary_mock"},
                paid_at=timezone.now(),
            )
            create_booking_prepayment(booking, payment)
    return user, booking



def public_booking_slots(request):
    _language, t, _services, _articles = _localized_context(request)
    service_id = request.GET.get("service")
    date_text = request.GET.get("date")
    zone_id = request.GET.get("zone")
    if not service_id or not date_text:
        return JsonResponse({"ok": False, "message": t["public_booking_select_service_date"]}, status=400)

    try:
        service = Service.objects.prefetch_related("allowed_zones", "employees").get(pk=service_id, is_active=True)
        date_value = datetime.strptime(date_text, "%Y-%m-%d").date()
    except (Service.DoesNotExist, ValueError):
        return JsonResponse({"ok": False, "message": t["public_booking_error_service"]}, status=400)

    if date_value < timezone.localdate():
        return JsonResponse({"ok": False, "message": t["public_booking_error_future"]}, status=400)

    zone = None
    if zone_id:
        try:
            zone = Zone.objects.get(pk=zone_id, is_active=True)
        except Zone.DoesNotExist:
            return JsonResponse({"ok": False, "message": t["public_booking_error_zone"]}, status=400)
        if service.requires_zone and not service.allowed_zones.filter(pk=zone.pk).exists():
            return JsonResponse({"ok": False, "message": t["public_booking_error_zone_service"]}, status=400)

    slot_map = {}
    blocked = []
    employees = (
        Employee.objects.filter(is_active=True, services=service)
        .prefetch_related("services")
        .order_by("first_name", "last_name")
    )
    for employee in employees:
        slots, employee_blocked = build_available_slots_for_day(
            date_obj=date_value,
            employee=employee,
            service=service,
            zone=zone,
            step_minutes=MOBILE_SLOT_STEP_MINUTES,
        )
        for item in employee_blocked:
            blocked.append(
                {
                    "start_at": _format_public_datetime(item["start_at"]),
                    "end_at": _format_public_datetime(item["end_at"]),
                    "reason": item["reason"],
                    "employee": employee.pk,
                    "employee_name": employee.full_name,
                }
            )
        for slot in slots:
            if not _is_future_public_slot(slot):
                continue
            slot_zone = zone
            if service.requires_zone and slot_zone is None:
                slot_zone = find_available_zone(service, slot["start_at"], slot["end_at"])
            if service.requires_zone and slot_zone is None:
                continue
            key = _format_public_datetime(slot["start_at"])
            item = slot_map.setdefault(
                key,
                {
                    "start_at": key,
                    "end_at": _format_public_datetime(slot["end_at"]),
                    "label": timezone.localtime(slot["start_at"]).strftime("%H:%M"),
                    "employees": [],
                },
            )
            item["employees"].append(
                {
                    "id": employee.pk,
                    "name": employee.full_name,
                    "zone": slot_zone.pk if slot_zone else None,
                    "zone_name": slot_zone.name if slot_zone else "",
                }
            )

    return JsonResponse(
        {
            "ok": True,
            "date": date_value.isoformat(),
            "service": service.pk,
            "duration": service.duration_minutes,
            "step_minutes": MOBILE_SLOT_STEP_MINUTES,
            "slots": sorted(slot_map.values(), key=lambda item: item["start_at"]),
            "blocked": blocked,
        }
    )


def public_booking(request):
    if request.method == "GET":
        return render(request, "core/public_booking.html", _public_booking_context(request, request.GET.dict()))

    _language, t, _services, _articles = _localized_context(request)
    post = request.POST
    if post.get("action") == "existing_account":
        pending = pending_booking_from_post(post)
        if request.user.is_authenticated:
            client = get_client_profile(request.user)
            if not client:
                redirect_url = reverse("dashboard:home")
            else:
                booking, errors = create_booking_for_client_from_pending(client, pending)
                if errors:
                    return _public_booking_error_response(request, post.dict(), errors)
                redirect_url = reverse("clients:portal")
            if _public_wants_json(request):
                return JsonResponse({"ok": True, "redirect": redirect_url})
            return redirect(redirect_url)
        request.session[PUBLIC_PENDING_BOOKING_SESSION_KEY] = pending
        redirect_url = f"{reverse('accounts:login')}?next={reverse('clients:portal')}"
        if _public_wants_json(request):
            return JsonResponse({"ok": True, "redirect": redirect_url})
        return redirect(redirect_url)

    include_contact = post.get("include_contact") == "on"
    values = {
        "service": post.get("service", ""),
        "employee": post.get("employee", ""),
        "zone": post.get("zone", ""),
        "start_at": post.get("start_at", ""),
        "date": post.get("date", ""),
        "name": post.get("name", "").strip(),
        "password": post.get("password", ""),
        "include_contact": "on" if include_contact else "",
        "phone": post.get("phone", "").strip() if include_contact else "",
        "email": post.get("email", "").strip() if include_contact else "",
    }
    errors = {}

    if not values["name"]:
        errors["name"] = [t["public_booking_error_name"]]
    if not values["password"]:
        errors["password"] = [t["public_booking_error_password_required"]]
    elif len(values["password"]) < 6:
        errors["password"] = [t["public_booking_error_password_min"]]
    if post.get("mock_payment_confirmed") != "1":
        errors["__all__"] = [t["public_booking_error_payment_required"]]

    service = employee = zone = start_at = None
    try:
        service = Service.objects.prefetch_related("allowed_zones", "employees").get(pk=values["service"], is_active=True)
    except (Service.DoesNotExist, ValueError, TypeError):
        errors["service"] = [t["public_booking_error_service"]]

    if service:
        try:
            employee = Employee.objects.get(pk=values["employee"], is_active=True, services=service)
        except (Employee.DoesNotExist, ValueError, TypeError):
            errors["employee"] = [t["public_booking_error_employee"]]

    if values.get("zone"):
        try:
            zone = Zone.objects.get(pk=values["zone"], is_active=True)
        except (Zone.DoesNotExist, ValueError, TypeError):
            errors["zone"] = [t["public_booking_error_zone"]]
        if zone and service and service.requires_zone and not service.allowed_zones.filter(pk=zone.pk).exists():
            errors["zone"] = [t["public_booking_error_zone_service"]]

    start_at = parse_datetime(values["start_at"] or "")
    if not start_at:
        errors["start_at"] = [t["public_booking_error_time"]]
    else:
        if timezone.is_naive(start_at):
            start_at = timezone.make_aware(start_at)
        start_at = timezone.localtime(start_at).replace(second=0, microsecond=0)
        if start_at < timezone.localtime(timezone.now()).replace(second=0, microsecond=0):
            errors["start_at"] = [t["public_booking_error_future"]]

    if include_contact and values["email"]:
        if User.objects.filter(email__iexact=values["email"]).exists() or Client.objects.filter(email__iexact=values["email"]).exists():
            errors["email"] = [t["public_booking_error_email_exists"]]
    if include_contact and values["phone"]:
        if User.objects.filter(phone=values["phone"]).exists() or Client.objects.filter(phone=values["phone"]).exists():
            errors["phone"] = [t["public_booking_error_phone_exists"]]

    end_at = None
    if service and employee and start_at and not errors.get("start_at"):
        end_at = start_at + timedelta(minutes=service.duration_minutes)
        if service.requires_zone and zone is None:
            zone = find_available_zone(service, start_at, end_at)
        if service.requires_zone and zone is None:
            errors["zone"] = [t["public_booking_error_no_zone"]]
        else:
            slots, _blocked = build_available_slots_for_day(
                date_obj=timezone.localtime(start_at).date(),
                employee=employee,
                service=service,
                zone=zone,
                step_minutes=MOBILE_SLOT_STEP_MINUTES,
            )
            if not _slot_matches(start_at, slots):
                errors["start_at"] = [t["public_booking_error_slot_taken"]]

    if errors:
        return _public_booking_error_response(request, values, errors)

    try:
        user, booking = _create_public_booking(
            {
                "name": values["name"],
                "password": values["password"],
                "phone": values["phone"],
                "email": values["email"],
                "service": service,
                "employee": employee,
                "zone": zone,
                "start_at": start_at,
                "end_at": end_at,
            }
        )
    except PublicBookingError as exc:
        return _public_booking_error_response(request, values, exc.errors)

    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    language = detect_public_language(request)
    request.session[PUBLIC_LANGUAGE_SESSION_KEY] = language
    request.session[CLIENT_LANGUAGE_SESSION_KEY] = language
    redirect_url = reverse("clients:portal")
    if _public_wants_json(request):
        return JsonResponse({"ok": True, "redirect": redirect_url, "username": user.username})
    return redirect(redirect_url)


@require_POST
def public_waitlist(request):
    language = detect_public_language(request)
    t = public_texts(language)
    service_id = request.POST.get("service")
    employee_id = request.POST.get("employee")
    desired_date_text = request.POST.get("date")
    name = (request.POST.get("name") or "").strip()
    email = (request.POST.get("email") or "").strip()
    phone = (request.POST.get("phone") or "").strip()
    time_range = (request.POST.get("time_range") or "").strip()

    errors = {}
    if not name:
        errors["name"] = [t["public_booking_error_name"]]
    if not email and not phone:
        errors["__all__"] = [t["public_waitlist_contact_required"]]

    try:
        service = Service.objects.get(pk=service_id, is_active=True)
    except (Service.DoesNotExist, ValueError, TypeError):
        service = None
        errors["service"] = [t["public_booking_error_service"]]

    try:
        employee = Employee.objects.get(pk=employee_id, is_active=True)
    except (Employee.DoesNotExist, ValueError, TypeError):
        employee = None
        errors["employee"] = [t["public_booking_error_employee"]]

    try:
        desired_date = datetime.strptime(desired_date_text or "", "%Y-%m-%d").date()
    except ValueError:
        desired_date = None
        errors["date"] = [t["public_booking_error_future"]]

    if desired_date and desired_date < timezone.localdate():
        errors["date"] = [t["public_booking_error_future"]]
    if service and employee and not employee.services.filter(pk=service.pk).exists():
        errors["employee"] = [t["public_booking_error_employee"]]

    if errors:
        return JsonResponse({"ok": False, "errors": errors}, status=400)

    entry = BookingWaitlistEntry.objects.create(
        service=service,
        employee=employee,
        desired_date=desired_date,
        time_range=time_range,
        name=name,
        email=email,
        phone=phone,
        source=Booking.Sources.WEBSITE,
    )
    return JsonResponse({"ok": True, "message": t["public_waitlist_success"], "waitlist_id": entry.pk})


@require_POST
def set_public_language(request):
    language = normalize_public_language(request.POST.get("language"))
    request.session[PUBLIC_LANGUAGE_SESSION_KEY] = language
    request.session[CLIENT_LANGUAGE_SESSION_KEY] = language
    response = redirect(request.POST.get("next") or reverse("home"))
    response.set_cookie(PUBLIC_LANGUAGE_SESSION_KEY, language, max_age=60 * 60 * 24 * 365, samesite="Lax", secure=True)
    response.set_cookie(CLIENT_LANGUAGE_SESSION_KEY, language, max_age=60 * 60 * 24 * 365, samesite="Lax", secure=True)
    return response


def home(request):
    language, t, services, articles = _localized_context(request)
    schema = json.dumps({
        "@context": "https://schema.org",
        "@type": "BeautySalon",
        "name": SITE_NAME,
        "url": _absolute_url(request, reverse("home")),
        "description": t["home_meta"],
        "address": {"@type": "PostalAddress", "addressLocality": "Bilbao", "addressCountry": "ES"},
        "openingHours": "Mo-Sa by appointment",
        "sameAs": [],
        "makesOffer": [{"@type": "Offer", "itemOffered": {"@type": "Service", "name": service["title"]}} for service in services],
    }, ensure_ascii=False)
    context = _base_context(request, reverse("home"))
    context.update({
        "services": services,
        "articles": articles[:3],
        "featured_instagram_posts": InstagramPost.objects.filter(active=True, featured=True).order_by("sort_order", "-created_at", "-id")[:3],
        "schema_json": schema,
    })
    return render(request, "core/home.html", context)


def service_index(request):
    language, t, services, articles = _localized_context(request)
    schema = json.dumps({
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": t["all_services_title"],
        "description": t["services_text"],
        "url": _absolute_url(request, reverse("service_index")),
    }, ensure_ascii=False)
    context = _base_context(request, reverse("service_index"))
    context.update({"services": services, "articles": articles[:3], "schema_json": schema, "meta_description": t["home_meta"]})
    return render(request, "core/service_index.html", context)


def service_detail(request, slug):
    language, t, services, articles = _localized_context(request)
    service = _find_by_slug(services, slug)
    related = [item for item in services if item["slug"] != slug][:3]
    context = _base_context(request, reverse("service_detail", args=[slug]))
    context.update({
        "service": service,
        "related_services": related,
        "articles": articles[:3],
        "schema_json": _service_schema(request, service),
    })
    return render(request, "core/service_detail.html", context)


def advice_index(request):
    language, t, services, articles = _localized_context(request)
    schema = json.dumps({
        "@context": "https://schema.org",
        "@type": "Blog",
        "name": t["tips_page_title"],
        "description": t["guide_text"],
        "url": _absolute_url(request, reverse("advice_index")),
    }, ensure_ascii=False)
    context = _base_context(request, reverse("advice_index"))
    context.update({"articles": articles, "services": services, "schema_json": schema, "meta_description": t["guide_text"]})
    return render(request, "core/advice_index.html", context)


def article_detail(request, slug):
    language, t, services, articles = _localized_context(request)
    article = _find_by_slug(articles, slug)
    related_articles = [item for item in articles if item["slug"] != slug][:3]
    context = _base_context(request, reverse("article_detail", args=[slug]))
    context.update({
        "article": article,
        "related_articles": related_articles,
        "services": services[:3],
        "schema_json": _article_schema(request, article),
    })
    return render(request, "core/article_detail.html", context)


def robots_txt(request):
    content = f"""User-agent: *
Allow: /
Disallow: /panel/
Disallow: /dj-admin/
Disallow: /api/
Sitemap: {SITE_DOMAIN}{reverse('sitemap_xml')}
"""
    return HttpResponse(content, content_type="text/plain")


def sitemap_xml(request):
    paths = [
        reverse("home"),
        reverse("public_booking"),
        reverse("service_index"),
        reverse("advice_index"),
    ]
    paths += [reverse("service_detail", args=[service["slug"]]) for service in SERVICES]
    paths += [reverse("article_detail", args=[article["slug"]]) for article in ARTICLES]
    urls = "\n".join(
        f"  <url><loc>{_absolute_url(request, path)}</loc><changefreq>weekly</changefreq><priority>{'1.0' if path == reverse('home') else '0.8'}</priority></url>"
        for path in paths
    )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{urls}
</urlset>
"""
    return HttpResponse(xml, content_type="application/xml")
