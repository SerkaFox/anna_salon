from datetime import datetime
from decimal import Decimal

from django.db.models.deletion import ProtectedError
from django.db.models import Count
from django.http import FileResponse, Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework import generics, serializers, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import can_access_booking, can_access_client, can_access_employee, get_client_profile, get_employee_profile, scope_bookings_queryset, scope_clients_queryset, scope_employees_queryset
from auditlog.services import log_event
from bookings.models import Booking, BookingPhoto
from bookings.utils import (
    MOBILE_SLOT_STEP_MINUTES,
    build_available_slots_for_day,
    find_available_zone,
    get_bookings_for_day,
    get_day_bounds,
    get_employee_schedule,
    get_employee_time_block_occurrences,
)
from clients.models import Client
from clients.models import ClientRewardRule
from clients.rewards import client_reward_progress
from employees.models import Employee, EmployeeRecurringTimeBlock, EmployeeTimeBlock
from salon.models import Zone
from services_app.models import Service

from .permissions import IsAuthenticatedMobileUser
from .serializers import (
    AvailabilityCheckSerializer,
    AvailabilitySlotsQuerySerializer,
    BookingSerializer,
    BookingStatusSerializer,
    BookingWriteSerializer,
    ClientWriteSerializer,
    ClientSerializer,
    ClientRewardRuleSerializer,
    ClientRewardRuleWriteSerializer,
    EmployeeWriteSerializer,
    EmployeeSerializer,
    ServiceSerializer,
    ServiceWriteSerializer,
    TimeBlockSerializer,
    TimeBlockWriteSerializer,
    ZoneSerializer,
    ZoneWriteSerializer,
    _format_local_datetime,
)


def _is_mobile_employee_user(user):
    return bool(getattr(user, "is_authenticated", False) and get_employee_profile(user))


def _mobile_employees_queryset(queryset, user):
    if _is_mobile_employee_user(user):
        return queryset
    return scope_employees_queryset(queryset, user)


def _mobile_bookings_queryset(queryset, user):
    if _is_mobile_employee_user(user):
        return queryset
    return scope_bookings_queryset(queryset, user)


def _mobile_can_access_booking(user, booking):
    if _is_mobile_employee_user(user):
        return True
    return can_access_booking(user, booking)


def _mobile_can_access_client(user, client):
    if _is_mobile_employee_user(user):
        return True
    return can_access_client(user, client)


def _normalize_id_aliases(data):
    normalized = data.copy()
    for alias, field in (
        ("client_id", "client"),
        ("employee_id", "employee"),
        ("service_id", "service"),
        ("zone_id", "zone"),
    ):
        if alias in normalized and field not in normalized:
            normalized[field] = normalized[alias]
    return normalized


class _MeUpdateSerializer(serializers.Serializer):
    first_name = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=150,
    )
    last_name = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=150,
    )
    email = serializers.EmailField(required=False, allow_blank=True)
    current_password = serializers.CharField(
        required=False,
        allow_blank=True,
        write_only=True,
    )
    new_password = serializers.CharField(
        required=False,
        allow_blank=True,
        write_only=True,
        min_length=4,
    )

    def validate(self, attrs):
        new_password = attrs.get("new_password")
        current_password = attrs.get("current_password")
        if not new_password:
            return attrs
        user = self.context["request"].user
        if not current_password:
            raise serializers.ValidationError(
                {"current_password": ["Introduce la contraseña actual."]}
            )
        if not user.check_password(current_password):
            raise serializers.ValidationError(
                {"current_password": ["La contraseña actual no es correcta."]}
            )
        return attrs


def _parse_date_param(request):
    date_str = request.query_params.get("date")
    if not date_str:
        return timezone.localdate()
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError as exc:
        raise serializers.ValidationError({"date": ["Fecha inválida. Usa el formato YYYY-MM-DD."]}) from exc


def _first_error_message(errors):
    if isinstance(errors, dict):
        for value in errors.values():
            message = _first_error_message(value)
            if message:
                return message
    if isinstance(errors, list) and errors:
        return _first_error_message(errors[0])
    if errors:
        return str(errors)
    return "Datos inválidos."


def _format_api_datetime(value):
    return timezone.localtime(value, timezone.get_default_timezone()).isoformat()


def _build_referral_tree(root_client):
    referred_clients = list(root_client.referred_clients.all().order_by("first_name", "last_name"))
    return {
        "id": root_client.pk,
        "name": root_client.full_name or str(root_client),
        "children": [_build_referral_tree(client) for client in referred_clients],
    }


def _serialize_named_count(row, name_fields=None, value_field="total"):
    if name_fields:
        name = " ".join(str(row.get(field) or "") for field in name_fields).strip()
    else:
        name = str(row.get("service__name") or "")
    return {"name": name, "count": row.get(value_field, 0)}


def _serialize_employee_count(row):
    return {
        "id": row.get("employee_id"),
        "name": " ".join(str(row.get(field) or "") for field in ("employee__first_name", "employee__last_name")).strip(),
        "count": row.get("total", 0),
    }


def _serialize_client_photo(photo):
    image_url = f"/api/v1/photos/{photo.pk}/image/"
    return {
        "id": photo.pk,
        "booking": photo.booking_id,
        "booking_start_at": _format_api_datetime(photo.booking.start_at),
        "service_name": photo.booking.service.name,
        "employee_name": photo.booking.employee.full_name,
        "photo_type": photo.photo_type,
        "photo_type_label": photo.get_photo_type_display(),
        "notes": photo.notes,
        "is_key_reference": photo.is_key_reference,
        "is_visible_to_client": photo.is_visible_to_client,
        "image_url": image_url,
    }


def _visible_photos_for_user(queryset, user):
    if user.can_manage_staff:
        return queryset
    return queryset.filter(is_visible_to_client=True)


def _parse_employee_stats_range(request):
    date_from = request.query_params.get("date_from")
    date_to = request.query_params.get("date_to")
    parsed_from = None
    parsed_to = None
    if date_from:
        try:
            parsed_from = datetime.strptime(date_from, "%Y-%m-%d").date()
        except ValueError:
            raise serializers.ValidationError({"date_from": ["Formato esperado YYYY-MM-DD."]})
    if date_to:
        try:
            parsed_to = datetime.strptime(date_to, "%Y-%m-%d").date()
        except ValueError:
            raise serializers.ValidationError({"date_to": ["Formato esperado YYYY-MM-DD."]})
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise serializers.ValidationError({"date_to": ["La fecha final no puede ser anterior a la inicial."]})
    return parsed_from, parsed_to


def _employee_detail_payload(employee, request):
    date_from, date_to = _parse_employee_stats_range(request)
    bookings = (
        Booking.objects.select_related("client", "service", "zone", "employee")
        .filter(employee=employee)
        .order_by("-start_at")
    )
    done_bookings = bookings.filter(status=Booking.Statuses.DONE)
    if date_from:
        done_bookings = done_bookings.filter(start_at__date__gte=date_from)
    if date_to:
        done_bookings = done_bookings.filter(start_at__date__lte=date_to)
    employee_earnings = Decimal("0.00")
    client_revenue = Decimal("0.00")
    salon_revenue = Decimal("0.00")
    clients = {}
    services = {}

    for booking in done_bookings:
        client_amount = booking.client_price_snapshot or Decimal("0.00")
        employee_amount = booking.employee_amount_snapshot or Decimal("0.00")
        salon_amount = booking.salon_amount_snapshot or Decimal("0.00")
        client_revenue += client_amount
        employee_earnings += employee_amount
        salon_revenue += salon_amount
        services[booking.service.name] = services.get(booking.service.name, 0) + 1
        client_stats = clients.setdefault(
            booking.client_id,
            {
                "id": booking.client_id,
                "name": booking.client.full_name or str(booking.client),
                "count": 0,
                "spent": Decimal("0.00"),
            },
        )
        client_stats["count"] += 1
        client_stats["spent"] += client_amount

    top_clients = sorted(
        clients.values(),
        key=lambda item: (-item["count"], -item["spent"], item["name"]),
    )[:5]
    top_services = sorted(
        ({"name": name, "count": count} for name, count in services.items()),
        key=lambda item: (-item["count"], item["name"]),
    )[:5]
    bookings_count = done_bookings.count()
    return {
        "employee": EmployeeSerializer(employee, context={"request": request}).data,
        "stats": {
            "date_from": date_from.isoformat() if date_from else "",
            "date_to": date_to.isoformat() if date_to else "",
            "employee_earnings": str(employee_earnings),
            "client_revenue": str(client_revenue),
            "salon_revenue": str(salon_revenue),
            "bookings_count": bookings_count,
            "clients_count": len(clients),
            "repeat_clients_count": sum(1 for item in clients.values() if item["count"] > 1),
            "avg_ticket": str(client_revenue / bookings_count if bookings_count else Decimal("0.00")),
        },
        "top_clients": [
            {**item, "spent": str(item["spent"])}
            for item in top_clients
        ],
        "top_services": top_services,
        "bookings": BookingSerializer(list(bookings[:20]), many=True, context={"request": request}).data,
    }


class MobileApiMixin:
    permission_classes = [IsAuthenticatedMobileUser]


class MeView(MobileApiMixin, APIView):
    def _payload(self, request):
        employee = getattr(request.user, "employee_profile", None)
        client = get_client_profile(request.user)
        return {
            "id": request.user.pk,
            "username": request.user.username,
            "first_name": request.user.first_name,
            "last_name": request.user.last_name,
            "email": request.user.email,
            "role": request.user.role,
            "can_manage_staff": request.user.can_manage_staff,
            "employee_id": employee.pk if employee else None,
            "employee_name": employee.full_name if employee else "",
            "client_id": client.pk if client else None,
            "client_name": client.full_name if client else "",
        }

    def get(self, request):
        return Response(self._payload(request))

    def patch(self, request):
        serializer = _MeUpdateSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        profile_fields = {
            key: value
            for key, value in serializer.validated_data.items()
            if key in {"first_name", "last_name", "email"}
        }
        for field, value in profile_fields.items():
            setattr(request.user, field, value)
        new_password = serializer.validated_data.get("new_password")
        if new_password:
            request.user.set_password(new_password)
            request.user.save()
        elif profile_fields:
            request.user.save(update_fields=[*profile_fields.keys()])
        return Response(self._payload(request))


class ClientListView(MobileApiMixin, generics.ListCreateAPIView):
    serializer_class = ClientSerializer

    def get_queryset(self):
        queryset = Client.objects.filter(is_active=True)
        if not _is_mobile_employee_user(self.request.user):
            queryset = scope_clients_queryset(queryset, self.request.user)
        return queryset.order_by("first_name", "last_name")

    def create(self, request, *args, **kwargs):
        if get_client_profile(request.user):
            raise PermissionDenied("Sin permiso para crear clientes.")
        serializer = ClientWriteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        client = serializer.save()
        log_event(
            actor=request.user,
            section="client",
            action="create",
            instance=client,
            message=f"Cliente creado desde API movil: {client.full_name}.",
        )
        return Response(ClientSerializer(client, context={"request": request}).data, status=status.HTTP_201_CREATED)


class ClientDetailView(MobileApiMixin, APIView):
    def get_object(self, request, pk):
        client = generics.get_object_or_404(Client.objects.select_related("referred_by"), pk=pk, is_active=True)
        if not _mobile_can_access_client(request.user, client):
            raise PermissionDenied("Sin acceso a este cliente.")
        return client

    def get(self, request, pk):
        client = self.get_object(request, pk)
        bookings = (
            Booking.objects.select_related("employee", "service", "zone", "client")
            .prefetch_related("photos")
            .filter(client=client)
            .order_by("-start_at")
        )
        if not request.user.can_manage_staff and not get_client_profile(request.user) and not _is_mobile_employee_user(request.user):
            bookings = bookings.filter(employee=request.user.employee_profile)

        booking_history = list(bookings[:20])
        done_bookings = bookings.filter(status=Booking.Statuses.DONE)
        total_spent = sum((booking.client_price_snapshot for booking in done_bookings), Decimal("0.00"))
        total_visits = done_bookings.count()
        avg_ticket = total_spent / total_visits if total_visits else Decimal("0.00")
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
            done_bookings.values("employee_id", "employee__first_name", "employee__last_name")
            .annotate(total=Count("id"))
            .order_by("-total")[:3]
        )
        referred_clients = client.referred_clients.all().order_by("first_name", "last_name")
        if not _is_mobile_employee_user(request.user):
            referred_clients = scope_clients_queryset(referred_clients, request.user)
        successful_referrals_count = referred_clients.filter(bookings__status=Booking.Statuses.DONE).distinct().count()
        available_rewards = max((successful_referrals_count // 5) - client.referral_rewards_used, 0)
        remaining_for_next_reward = 5 - (successful_referrals_count % 5) if successful_referrals_count % 5 else 0
        photo_history = (
            BookingPhoto.objects.select_related("booking", "booking__service", "booking__employee", "client")
            .filter(client=client)
            .order_by("-created_at")
        )
        photo_history = _visible_photos_for_user(photo_history, request.user)
        photo_history = photo_history[:24]

        return Response(
            {
                "client": ClientSerializer(client, context={"request": request}).data,
                "stats": {
                    "total_visits": total_visits,
                    "total_spent": str(total_spent),
                    "avg_ticket": str(avg_ticket),
                    "cancelled": bookings.filter(status=Booking.Statuses.CANCELLED).count(),
                    "no_show": bookings.filter(status=Booking.Statuses.NO_SHOW).count(),
                },
                "last_visit": BookingSerializer(last_visit, context={"request": request}).data if last_visit else None,
                "next_booking": BookingSerializer(next_booking, context={"request": request}).data if next_booking else None,
                "top_services": [_serialize_named_count(row) for row in top_services],
                "top_employees": [_serialize_employee_count(row) for row in top_employees],
                "referred_clients": ClientSerializer(referred_clients, many=True, context={"request": request}).data,
                "referred_clients_count": referred_clients.count(),
                "referral_tree": _build_referral_tree(client),
                "successful_referrals_count": successful_referrals_count,
                "available_rewards": available_rewards,
                "remaining_for_next_reward": remaining_for_next_reward,
                "rewards": client_reward_progress(client),
                "bookings": BookingSerializer(booking_history, many=True, context={"request": request}).data,
                "photo_history": [_serialize_client_photo(photo) for photo in photo_history],
                "photo_history_count": len(photo_history),
            }
        )

    def patch(self, request, pk):
        client = self.get_object(request, pk)
        serializer = ClientWriteSerializer(instance=client, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        client = serializer.save()
        log_event(
            actor=request.user,
            section="client",
            action="update",
            instance=client,
            message=f"Cliente actualizado desde API movil: {client.full_name}.",
        )
        return Response(ClientSerializer(client, context={"request": request}).data)

    def delete(self, request, pk):
        if not request.user.can_manage_staff:
            raise PermissionDenied("Sin permiso para eliminar clientes.")
        client = self.get_object(request, pk)
        client_name = client.full_name
        try:
            client.delete()
            action = "delete"
            message = f"Cliente eliminado desde API movil: {client_name}."
        except ProtectedError:
            client.is_active = False
            update_fields = ["is_active", "updated_at"]
            client.save(update_fields=update_fields)
            if client.user:
                client.user.is_active = False
                client.user.save(update_fields=["is_active"])
            action = "deactivate"
            message = f"Cliente desactivado desde API movil: {client_name}."
        log_event(
            actor=request.user,
            section="client",
            action=action,
            instance=None if action == "delete" else client,
            message=message,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class ClientAvatarView(MobileApiMixin, APIView):
    parser_classes = [MultiPartParser, FormParser]

    def get_object(self, request, pk):
        client = generics.get_object_or_404(Client, pk=pk, is_active=True)
        if not can_access_client(request.user, client):
            raise PermissionDenied("Sin acceso a este cliente.")
        return client

    def get(self, request, pk):
        client = self.get_object(request, pk)
        if not client.avatar:
            raise Http404("Avatar no encontrado.")
        return FileResponse(client.avatar.open("rb"), content_type="image/jpeg")

    def post(self, request, pk):
        client = self.get_object(request, pk)
        image = request.FILES.get("image")
        if not image:
            return Response({"image": ["Selecciona una imagen."]}, status=status.HTTP_400_BAD_REQUEST)
        client.avatar = image
        client.save(update_fields=["avatar", "updated_at"])
        log_event(
            actor=request.user,
            section="client",
            action="avatar_update",
            instance=client,
            message=f"Avatar actualizado desde API movil: {client.full_name}.",
        )
        return Response(ClientSerializer(client, context={"request": request}).data)


class ClientRewardRuleListView(MobileApiMixin, APIView):
    def get(self, request):
        rules = ClientRewardRule.objects.all().order_by("sort_order", "name")
        if not request.user.can_manage_staff:
            rules = rules.filter(is_active=True)
        return Response({"results": ClientRewardRuleSerializer(rules, many=True).data})


class ClientRewardRuleDetailView(MobileApiMixin, APIView):
    def patch(self, request, pk):
        rule = generics.get_object_or_404(ClientRewardRule, pk=pk)
        serializer = ClientRewardRuleWriteSerializer(
            rule,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        rule = serializer.save()
        log_event(
            actor=request.user,
            section="client_reward",
            action="update",
            instance=rule,
            message=f"Premio actualizado desde API movil: {rule.name}.",
        )
        return Response(ClientRewardRuleSerializer(rule).data)


class ClientRewardProgressView(MobileApiMixin, APIView):
    def get(self, request, pk):
        client = generics.get_object_or_404(Client, pk=pk, is_active=True)
        if not can_access_client(request.user, client):
            raise PermissionDenied("Sin acceso a este cliente.")
        return Response({"results": client_reward_progress(client)})


class EmployeeListView(MobileApiMixin, generics.ListAPIView):
    serializer_class = EmployeeSerializer

    def get_queryset(self):
        return (
            _mobile_employees_queryset(Employee.objects.filter(is_active=True), self.request.user)
            .prefetch_related("services")
            .order_by("first_name", "last_name")
        )

    def post(self, request):
        serializer = EmployeeWriteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        employee = serializer.save()
        log_event(
            actor=request.user,
            section="employee",
            action="create",
            instance=employee,
            message=f"Empleado creado desde API movil: {employee.full_name}.",
        )
        return Response(EmployeeSerializer(employee, context={"request": request}).data, status=status.HTTP_201_CREATED)


class EmployeeDetailView(MobileApiMixin, APIView):
    def get_object(self, request, pk):
        employee = generics.get_object_or_404(
            Employee.objects.select_related("user").prefetch_related("services"),
            pk=pk,
        )
        if not can_access_employee(request.user, employee):
            raise PermissionDenied("Sin acceso a este empleado.")
        return employee

    def get(self, request, pk):
        employee = self.get_object(request, pk)
        return Response(_employee_detail_payload(employee, request))

    def patch(self, request, pk):
        employee = self.get_object(request, pk)
        serializer = EmployeeWriteSerializer(
            instance=employee,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        employee = serializer.save()
        log_event(
            actor=request.user,
            section="employee",
            action="update",
            instance=employee,
            message=f"Empleado actualizado desde API movil: {employee.full_name}.",
        )
        return Response(_employee_detail_payload(employee, request))

    def delete(self, request, pk):
        if not request.user.can_manage_staff:
            raise PermissionDenied("Sin permiso para eliminar empleados.")
        employee = self.get_object(request, pk)
        employee_name = employee.full_name
        try:
            employee.delete()
            action = "delete"
            message = f"Empleado eliminado desde API movil: {employee_name}."
        except ProtectedError:
            employee.is_active = False
            employee.save(update_fields=["is_active", "updated_at"])
            if employee.user:
                employee.user.is_active = False
                employee.user.save(update_fields=["is_active"])
            action = "deactivate"
            message = f"Empleado desactivado desde API movil: {employee_name}."
        log_event(
            actor=request.user,
            section="employee",
            action=action,
            instance=None if action == "delete" else employee,
            message=message,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class ServiceListView(MobileApiMixin, generics.ListCreateAPIView):
    serializer_class = ServiceSerializer

    def get_queryset(self):
        return Service.objects.filter(is_active=True).prefetch_related("allowed_zones", "employees").order_by("name")

    def create(self, request, *args, **kwargs):
        serializer = ServiceWriteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        service = serializer.save()
        log_event(
            actor=request.user,
            section="service",
            action="create",
            instance=service,
            message=f"Servicio creado desde API movil: {service.name}.",
        )
        return Response(ServiceSerializer(service, context={"request": request}).data, status=status.HTTP_201_CREATED)


class ServiceDetailView(MobileApiMixin, APIView):
    def get_object(self, pk):
        return generics.get_object_or_404(Service.objects.prefetch_related("allowed_zones", "employees"), pk=pk)

    def get(self, request, pk):
        return Response(ServiceSerializer(self.get_object(pk), context={"request": request}).data)

    def patch(self, request, pk):
        service = self.get_object(pk)
        serializer = ServiceWriteSerializer(instance=service, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        service = serializer.save()
        log_event(
            actor=request.user,
            section="service",
            action="update",
            instance=service,
            message=f"Servicio actualizado desde API movil: {service.name}.",
        )
        return Response(ServiceSerializer(service, context={"request": request}).data)

    def delete(self, request, pk):
        if not request.user.can_manage_staff:
            raise PermissionDenied("Sin permiso para eliminar servicios.")
        service = self.get_object(pk)
        service_name = service.name
        try:
            service.delete()
            action = "delete"
            message = f"Servicio eliminado desde API movil: {service_name}."
            instance = None
        except ProtectedError:
            service.is_active = False
            service.save(update_fields=["is_active", "updated_at"])
            action = "deactivate"
            message = f"Servicio desactivado desde API movil: {service_name}."
            instance = service
        log_event(
            actor=request.user,
            section="service",
            action=action,
            instance=instance,
            message=message,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class ZoneListView(MobileApiMixin, generics.ListCreateAPIView):
    serializer_class = ZoneSerializer

    def get_queryset(self):
        return Zone.objects.filter(is_active=True).order_by("name")

    def create(self, request, *args, **kwargs):
        serializer = ZoneWriteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        zone = serializer.save()
        log_event(
            actor=request.user,
            section="zone",
            action="create",
            instance=zone,
            message=f"Zona creada desde API movil: {zone.name}.",
        )
        return Response(ZoneSerializer(zone, context={"request": request}).data, status=status.HTTP_201_CREATED)


class ZoneDetailView(MobileApiMixin, APIView):
    def get_object(self, pk):
        return generics.get_object_or_404(Zone, pk=pk)

    def get(self, request, pk):
        return Response(ZoneSerializer(self.get_object(pk), context={"request": request}).data)

    def patch(self, request, pk):
        zone = self.get_object(pk)
        serializer = ZoneWriteSerializer(instance=zone, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        zone = serializer.save()
        log_event(
            actor=request.user,
            section="zone",
            action="update",
            instance=zone,
            message=f"Zona actualizada desde API movil: {zone.name}.",
        )
        return Response(ZoneSerializer(zone, context={"request": request}).data)

    def delete(self, request, pk):
        if not request.user.can_manage_staff:
            raise PermissionDenied("Sin permiso para eliminar zonas.")
        zone = self.get_object(pk)
        zone_name = zone.name
        try:
            zone.delete()
            action = "delete"
            message = f"Zona eliminada desde API movil: {zone_name}."
            instance = None
        except ProtectedError:
            zone.is_active = False
            zone.save(update_fields=["is_active", "updated_at"])
            action = "deactivate"
            message = f"Zona desactivada desde API movil: {zone_name}."
            instance = zone
        log_event(
            actor=request.user,
            section="zone",
            action=action,
            instance=instance,
            message=message,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class BookingListCreateView(MobileApiMixin, generics.ListCreateAPIView):
    serializer_class = BookingSerializer

    def get_queryset(self):
        queryset = Booking.objects.select_related("client", "employee", "service", "zone")
        return _mobile_bookings_queryset(queryset, self.request.user).order_by("start_at", "pk")

    def list(self, request, *args, **kwargs):
        selected_date = _parse_date_param(request)
        day_start, day_end = get_day_bounds(selected_date)
        queryset = self.get_queryset().filter(start_at__lte=day_end, end_at__gte=day_start)
        serializer = self.get_serializer(queryset, many=True)
        return Response({"date": selected_date.isoformat(), "results": serializer.data})

    def create(self, request, *args, **kwargs):
        serializer = BookingWriteSerializer(data=_normalize_id_aliases(request.data), context={"request": request})
        serializer.is_valid(raise_exception=True)
        booking = serializer.save()
        log_event(
            actor=request.user,
            section="booking",
            action="create",
            instance=booking,
            message=f"Reserva creada desde API móvil para {booking.client}.",
        )
        return Response(BookingSerializer(booking, context={"request": request}).data, status=status.HTTP_201_CREATED)


class BookingDetailView(MobileApiMixin, APIView):
    def get_object(self, request, pk):
        booking = generics.get_object_or_404(Booking.objects.select_related("client", "employee", "service", "zone"), pk=pk)
        if not _mobile_can_access_booking(request.user, booking):
            raise PermissionDenied("Sin acceso a esta reserva.")
        return booking

    def get(self, request, pk):
        booking = self.get_object(request, pk)
        return Response(BookingSerializer(booking, context={"request": request}).data)

    def patch(self, request, pk):
        booking = self.get_object(request, pk)
        serializer = BookingWriteSerializer(
            instance=booking,
            data=_normalize_id_aliases(request.data),
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        booking = serializer.save()
        log_event(
            actor=request.user,
            section="booking",
            action="update",
            instance=booking,
            message=f"Reserva actualizada desde API móvil para {booking.client}.",
        )
        return Response(BookingSerializer(booking, context={"request": request}).data)


class BookingAvailabilityCheckView(MobileApiMixin, APIView):
    def post(self, request):
        serializer = AvailabilityCheckSerializer(data=_normalize_id_aliases(request.data), context={"request": request})
        if not serializer.is_valid():
            return Response(
                {
                    "ok": False,
                    "available": False,
                    "message": _first_error_message(serializer.errors),
                    "errors": serializer.errors,
                },
                status=status.HTTP_200_OK,
            )
        data = serializer.validated_data
        return Response(
            {
                "ok": True,
                "available": True,
                "message": "Horario disponible.",
                "employee": data["employee"].pk,
                "service": data["service"].pk,
                "zone": data["zone"].pk if data.get("zone") else None,
                "start_at": _format_local_datetime(data["start_at"]),
                "end_at": _format_local_datetime(data["end_at"]),
            }
        )


class AvailabilitySlotsView(MobileApiMixin, APIView):
    def get(self, request):
        serializer = AvailabilitySlotsQuerySerializer(data=request.query_params, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        booking = data.get("booking")
        if not data.get("employee"):
            return self._team_slots(request, data, booking)
        slots, blocked = build_available_slots_for_day(
            date_obj=data["date"],
            employee=data["employee"],
            service=data["service"],
            zone=data.get("zone"),
            exclude_booking_id=booking.pk if booking else None,
            step_minutes=MOBILE_SLOT_STEP_MINUTES,
        )
        return Response(
            {
                "date": data["date"].isoformat(),
                "employee": data["employee"].pk,
                "service": data["service"].pk,
                "zone": data["zone"].pk if data.get("zone") else None,
                "duration": data["service"].duration_minutes,
                "step_minutes": MOBILE_SLOT_STEP_MINUTES,
                "slots": [
                    {
                        "start_at": _format_api_datetime(slot["start_at"]),
                        "end_at": _format_api_datetime(slot["end_at"]),
                        "label": timezone.localtime(slot["start_at"]).strftime("%H:%M"),
                        "zone": (
                            data["zone"].pk
                            if data.get("zone")
                            else (
                                find_available_zone(data["service"], slot["start_at"], slot["end_at"], exclude_booking_id=booking.pk if booking else None).pk
                                if data["service"].requires_zone
                                and find_available_zone(data["service"], slot["start_at"], slot["end_at"], exclude_booking_id=booking.pk if booking else None)
                                else None
                            )
                        ),
                    }
                    for slot in slots
                ],
                "blocked": [
                    {
                        "start_at": _format_api_datetime(item["start_at"]),
                        "end_at": _format_api_datetime(item["end_at"]),
                        "reason": item["reason"],
                    }
                    for item in blocked
                ],
            }
        )

    def _team_slots(self, request, data, booking):
        service = data["service"]
        employees = _mobile_employees_queryset(
            Employee.objects.filter(is_active=True).prefetch_related("services"),
            request.user,
        ).filter(services=service).order_by("first_name", "last_name")
        slot_map = {}
        employee_payload = []
        for employee in employees:
            slots, _blocked = build_available_slots_for_day(
                date_obj=data["date"],
                employee=employee,
                service=service,
                zone=data.get("zone"),
                exclude_booking_id=booking.pk if booking else None,
                step_minutes=MOBILE_SLOT_STEP_MINUTES,
            )
            first_slot = slots[0] if slots else None
            employee_payload.append(
                {
                    "id": employee.pk,
                    "name": employee.full_name,
                    "next_start_at": _format_api_datetime(first_slot["start_at"]) if first_slot else None,
                    "next_label": timezone.localtime(first_slot["start_at"]).strftime("%H:%M") if first_slot else None,
                }
            )
            for slot in slots:
                key = _format_api_datetime(slot["start_at"])
                zone = data.get("zone")
                if zone is None and service.requires_zone:
                    zone = find_available_zone(service, slot["start_at"], slot["end_at"], exclude_booking_id=booking.pk if booking else None)
                item = slot_map.setdefault(
                    key,
                    {
                        "start_at": _format_api_datetime(slot["start_at"]),
                        "end_at": _format_api_datetime(slot["end_at"]),
                        "label": timezone.localtime(slot["start_at"]).strftime("%H:%M"),
                        "employees": [],
                    },
                )
                item["employees"].append(
                    {
                        "id": employee.pk,
                        "name": employee.full_name,
                        "zone": zone.pk if zone else None,
                        "zone_name": zone.name if zone else None,
                    }
                )
        slots = sorted(slot_map.values(), key=lambda item: item["start_at"])
        return Response(
            {
                "date": data["date"].isoformat(),
                "service": service.pk,
                "zone": data["zone"].pk if data.get("zone") else None,
                "duration": service.duration_minutes,
                "step_minutes": MOBILE_SLOT_STEP_MINUTES,
                "slots": slots,
                "employees": employee_payload,
                "blocked": [],
            }
        )


class BookingRescheduleView(MobileApiMixin, APIView):
    def post(self, request, pk):
        booking = generics.get_object_or_404(Booking.objects.select_related("client", "employee", "service", "zone"), pk=pk)
        if not _mobile_can_access_booking(request.user, booking):
            raise PermissionDenied("Sin acceso a esta reserva.")

        payload = _normalize_id_aliases(request.data)
        serializer = BookingWriteSerializer(instance=booking, data=payload, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        booking = serializer.save()
        log_event(
            actor=request.user,
            section="booking",
            action="reschedule",
            instance=booking,
            message=f"Reserva movida desde API móvil para {booking.client}.",
            metadata={"start_at": timezone.localtime(booking.start_at).isoformat(), "employee_id": booking.employee_id},
        )
        return Response(BookingSerializer(booking, context={"request": request}).data)


class BookingStatusView(MobileApiMixin, APIView):
    def post(self, request, pk):
        booking = generics.get_object_or_404(Booking.objects.select_related("client", "employee", "service", "zone"), pk=pk)
        if not _mobile_can_access_booking(request.user, booking):
            raise PermissionDenied("Sin acceso a esta reserva.")

        serializer = BookingStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking.status = serializer.validated_data["status"]
        booking.save(update_fields=["status", "updated_at"])
        log_event(
            actor=request.user,
            section="booking",
            action="status",
            instance=booking,
            message=f"Estado cambiado desde API móvil a {booking.get_status_display()} para {booking.client}.",
            metadata={"status": booking.status},
        )
        return Response(BookingSerializer(booking, context={"request": request}).data)


class BookingPhotoListCreateView(MobileApiMixin, APIView):
    parser_classes = [MultiPartParser, FormParser]

    def get_booking(self, request, pk):
        booking = generics.get_object_or_404(Booking.objects.select_related("client", "employee", "service", "zone"), pk=pk)
        if not _mobile_can_access_booking(request.user, booking):
            raise PermissionDenied("Sin acceso a esta reserva.")
        return booking

    def get(self, request, pk):
        booking = self.get_booking(request, pk)
        photos = booking.photos.select_related("booking", "booking__service", "booking__employee").order_by("-created_at")
        photos = _visible_photos_for_user(photos, request.user)
        return Response({"results": [_serialize_client_photo(photo) for photo in photos]})

    def post(self, request, pk):
        booking = self.get_booking(request, pk)
        image = request.FILES.get("image")
        if not image:
            return Response({"image": ["Selecciona una imagen."]}, status=status.HTTP_400_BAD_REQUEST)
        photo_type = request.data.get("photo_type") or BookingPhoto.PhotoTypes.BEFORE
        valid_types = {value for value, _label in BookingPhoto.PhotoTypes.choices}
        if photo_type not in valid_types:
            return Response({"photo_type": ["Tipo de foto invalido."]}, status=status.HTTP_400_BAD_REQUEST)
        photo = BookingPhoto.objects.create(
            booking=booking,
            client=booking.client,
            image=image,
            photo_type=photo_type,
            notes=request.data.get("notes", ""),
            is_key_reference=str(request.data.get("is_key_reference", "")).lower() in {"1", "true", "yes", "on"},
            is_visible_to_client=str(request.data.get("is_visible_to_client", "")).lower() in {"1", "true", "yes", "on"},
        )
        log_event(
            actor=request.user,
            section="booking_photo",
            action="upload",
            instance=booking,
            message=f"Foto subida desde API movil para {booking.client}.",
            metadata={"photo_type": photo.photo_type},
        )
        return Response(_serialize_client_photo(photo), status=status.HTTP_201_CREATED)


class BookingPhotoDetailView(MobileApiMixin, APIView):
    def get_object(self, request, pk):
        photo = generics.get_object_or_404(BookingPhoto.objects.select_related("booking"), pk=pk)
        if not _mobile_can_access_booking(request.user, photo.booking):
            raise PermissionDenied("Sin acceso a esta foto.")
        return photo

    def patch(self, request, pk):
        if not request.user.can_manage_staff:
            raise PermissionDenied("Solo administracion puede cambiar la visibilidad de fotos.")
        photo = self.get_object(request, pk)
        if "is_visible_to_client" not in request.data:
            return Response({"is_visible_to_client": ["Campo requerido."]}, status=status.HTTP_400_BAD_REQUEST)
        photo.is_visible_to_client = str(request.data.get("is_visible_to_client", "")).lower() in {"1", "true", "yes", "on"}
        photo.save(update_fields=["is_visible_to_client"])
        log_event(
            actor=request.user,
            section="booking_photo",
            action="visibility",
            instance=photo.booking,
            message=f"Visibilidad de foto cambiada desde API movil para {photo.client}.",
            metadata={"photo_id": photo.pk, "is_visible_to_client": photo.is_visible_to_client},
        )
        return Response(_serialize_client_photo(photo))


class BookingPhotoImageView(MobileApiMixin, APIView):
    def get(self, request, pk):
        photo = generics.get_object_or_404(BookingPhoto.objects.select_related("booking"), pk=pk)
        if not _mobile_can_access_booking(request.user, photo.booking):
            raise PermissionDenied("Sin acceso a esta foto.")
        if not request.user.can_manage_staff and not photo.is_visible_to_client:
            raise PermissionDenied("Esta foto no esta visible.")
        return FileResponse(photo.image.open("rb"), content_type="image/jpeg")


class TimeBlockListCreateView(MobileApiMixin, APIView):
    def get(self, request):
        selected_date = _parse_date_param(request)
        employee_id = request.query_params.get("employee")
        employees = _mobile_employees_queryset(Employee.objects.filter(is_active=True), request.user)
        if employee_id:
            employees = employees.filter(pk=employee_id)
        occurrences = []
        for employee in employees.order_by("first_name", "last_name"):
            occurrences.extend(get_employee_time_block_occurrences(employee, selected_date))
        return Response(
            {
                "date": selected_date.isoformat(),
                "results": TimeBlockSerializer(occurrences, many=True).data,
            }
        )

    def post(self, request):
        serializer = TimeBlockWriteSerializer(data=_normalize_id_aliases(request.data), context={"request": request})
        serializer.is_valid(raise_exception=True)
        block = serializer.save()
        if isinstance(block, EmployeeTimeBlock):
            log_event(
                actor=request.user,
                section="calendar",
                action="time_block_create",
                instance=block,
                message=f"Bloqueo creado desde API móvil para {block.employee.full_name}.",
                metadata={"label": block.label},
            )
            return Response(TimeBlockSerializer(block).data, status=status.HTTP_201_CREATED)

        log_event(
            actor=request.user,
            section="calendar",
            action="recurring_time_block_create",
            instance=block,
            message=f"Bloqueo recurrente creado desde API móvil para {block.employee.full_name}.",
            metadata={"label": block.label},
        )
        return Response(
            _serialize_recurring_time_block(block),
            status=status.HTTP_201_CREATED,
        )


def _parse_recurring_time_block_id(pk):
    value = str(pk)
    if not value.startswith("recurring-"):
        return None
    try:
        return int(value.removeprefix("recurring-"))
    except ValueError:
        return None


def _serialize_recurring_time_block(block):
    return {
        "id": f"recurring-{block.pk}",
        "employee": block.employee_id,
        "weekday": block.weekday,
        "start_time": block.start_time.strftime("%H:%M:%S"),
        "end_time": block.end_time.strftime("%H:%M:%S"),
        "reason": block.label or "Bloqueo",
        "label": block.label or "Bloqueo",
        "color": block.color,
        "active": block.active,
        "date_from": block.date_from.isoformat(),
        "date_to": block.date_to.isoformat() if block.date_to else None,
        "is_recurring": True,
    }


class TimeBlockDetailView(MobileApiMixin, APIView):
    def get_object(self, request, pk):
        recurring_id = _parse_recurring_time_block_id(pk)
        if recurring_id is not None:
            block = generics.get_object_or_404(EmployeeRecurringTimeBlock.objects.select_related("employee"), pk=recurring_id)
            if not can_access_employee(request.user, block.employee):
                raise PermissionDenied("Sin acceso a este bloqueo.")
            return block

        block = generics.get_object_or_404(EmployeeTimeBlock.objects.select_related("employee"), pk=pk)
        if not can_access_employee(request.user, block.employee):
            raise PermissionDenied("Sin acceso a este bloqueo.")
        return block

    def patch(self, request, pk):
        block = self.get_object(request, pk)
        if isinstance(block, EmployeeRecurringTimeBlock):
            serializer = TimeBlockWriteSerializer(
                instance=block,
                data={**_normalize_id_aliases(request.data), "recurring": True},
                partial=True,
                context={"request": request},
            )
            serializer.is_valid(raise_exception=True)
            block = serializer.save()
            log_event(
                actor=request.user,
                section="calendar",
                action="recurring_time_block_update",
                instance=block,
                message=f"Bloqueo recurrente actualizado desde API móvil para {block.employee.full_name}.",
                metadata={"label": block.label},
            )
            return Response(_serialize_recurring_time_block(block))

        serializer = TimeBlockWriteSerializer(
            instance=block,
            data=_normalize_id_aliases(request.data),
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        block = serializer.save()
        log_event(
            actor=request.user,
            section="calendar",
            action="time_block_update",
            instance=block,
            message=f"Bloqueo actualizado desde API móvil para {block.employee.full_name}.",
            metadata={"label": block.label},
        )
        return Response(TimeBlockSerializer(block).data)

    def delete(self, request, pk):
        block = self.get_object(request, pk)
        employee_name = block.employee.full_name
        label = block.label
        is_recurring = isinstance(block, EmployeeRecurringTimeBlock)
        block.delete()
        log_event(
            actor=request.user,
            section="calendar",
            action="recurring_time_block_delete" if is_recurring else "time_block_delete",
            message=f"Bloqueo eliminado desde API móvil para {employee_name}.",
            metadata={"label": label},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class CalendarDayView(MobileApiMixin, APIView):
    def get(self, request):
        selected_date = _parse_date_param(request)
        bookings = _mobile_bookings_queryset(get_bookings_for_day(selected_date), request.user)
        employees = _mobile_employees_queryset(Employee.objects.filter(is_active=True), request.user).order_by("first_name", "last_name")
        employee_payload = []

        for employee in employees:
            schedule = get_employee_schedule(employee, selected_date)
            employee_payload.append(
                {
                    "employee": EmployeeSerializer(employee, context={"request": request}).data,
                    "schedule": {
                        "start_at": _format_local_datetime(schedule["start_at"]),
                        "end_at": _format_local_datetime(schedule["end_at"]),
                        "break_start_at": _format_local_datetime(schedule.get("break_start_at")),
                        "break_end_at": _format_local_datetime(schedule.get("break_end_at")),
                        "break_label": schedule.get("break_label", ""),
                        "label": schedule.get("label", ""),
                    }
                    if schedule
                    else None,
                    "time_blocks": TimeBlockSerializer(get_employee_time_block_occurrences(employee, selected_date), many=True).data,
                }
            )

        return Response(
            {
                "date": selected_date.isoformat(),
                "bookings": BookingSerializer(bookings, many=True, context={"request": request}).data,
                "employees": employee_payload,
            }
        )
