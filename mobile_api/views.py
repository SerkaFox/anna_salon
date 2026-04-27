from datetime import datetime

from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework import generics, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import can_access_booking, scope_bookings_queryset, scope_clients_queryset, scope_employees_queryset
from auditlog.services import log_event
from bookings.models import Booking
from bookings.utils import get_bookings_for_day, get_day_bounds, get_employee_schedule, get_employee_time_blocks
from clients.models import Client
from employees.models import Employee
from salon.models import Zone
from services_app.models import Service

from .permissions import IsAuthenticatedMobileUser
from .serializers import (
    AvailabilityCheckSerializer,
    BookingSerializer,
    BookingStatusSerializer,
    BookingWriteSerializer,
    ClientSerializer,
    EmployeeSerializer,
    ServiceSerializer,
    TimeBlockSerializer,
    ZoneSerializer,
    _format_local_datetime,
)


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


def _parse_date_param(request):
    date_str = request.query_params.get("date")
    if not date_str:
        return timezone.localdate()
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError as exc:
        raise serializers.ValidationError({"date": ["Fecha inválida. Usa el formato YYYY-MM-DD."]}) from exc


class MobileApiMixin:
    permission_classes = [IsAuthenticatedMobileUser]


class MeView(MobileApiMixin, APIView):
    def get(self, request):
        employee = getattr(request.user, "employee_profile", None)
        return Response(
            {
                "id": request.user.pk,
                "username": request.user.username,
                "first_name": request.user.first_name,
                "last_name": request.user.last_name,
                "email": request.user.email,
                "role": request.user.role,
                "can_manage_staff": request.user.can_manage_staff,
                "employee_id": employee.pk if employee else None,
                "employee_name": employee.full_name if employee else "",
            }
        )


class ClientListView(MobileApiMixin, generics.ListAPIView):
    serializer_class = ClientSerializer

    def get_queryset(self):
        return scope_clients_queryset(Client.objects.filter(is_active=True), self.request.user).order_by("first_name", "last_name")


class EmployeeListView(MobileApiMixin, generics.ListAPIView):
    serializer_class = EmployeeSerializer

    def get_queryset(self):
        return (
            scope_employees_queryset(Employee.objects.filter(is_active=True), self.request.user)
            .prefetch_related("services")
            .order_by("first_name", "last_name")
        )


class ServiceListView(MobileApiMixin, generics.ListAPIView):
    serializer_class = ServiceSerializer

    def get_queryset(self):
        return Service.objects.filter(is_active=True).prefetch_related("allowed_zones", "employees").order_by("name")


class ZoneListView(MobileApiMixin, generics.ListAPIView):
    serializer_class = ZoneSerializer

    def get_queryset(self):
        return Zone.objects.filter(is_active=True).order_by("name")


class BookingListCreateView(MobileApiMixin, generics.ListCreateAPIView):
    serializer_class = BookingSerializer

    def get_queryset(self):
        queryset = Booking.objects.select_related("client", "employee", "service", "zone")
        return scope_bookings_queryset(queryset, self.request.user).order_by("start_at", "pk")

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
        if not can_access_booking(request.user, booking):
            raise PermissionDenied("Sin acceso a esta reserva.")
        return booking

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
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        return Response(
            {
                "ok": True,
                "message": "Horario disponible.",
                "employee": data["employee"].pk,
                "service": data["service"].pk,
                "zone": data["zone"].pk if data.get("zone") else None,
                "start_at": _format_local_datetime(data["start_at"]),
                "end_at": _format_local_datetime(data["end_at"]),
            }
        )


class BookingRescheduleView(MobileApiMixin, APIView):
    def post(self, request, pk):
        booking = generics.get_object_or_404(Booking.objects.select_related("client", "employee", "service", "zone"), pk=pk)
        if not can_access_booking(request.user, booking):
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
        if not can_access_booking(request.user, booking):
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


class CalendarDayView(MobileApiMixin, APIView):
    def get(self, request):
        selected_date = _parse_date_param(request)
        bookings = scope_bookings_queryset(get_bookings_for_day(selected_date), request.user)
        employees = scope_employees_queryset(Employee.objects.filter(is_active=True), request.user).order_by("first_name", "last_name")
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
                    "time_blocks": TimeBlockSerializer(get_employee_time_blocks(employee, selected_date), many=True).data,
                }
            )

        return Response(
            {
                "date": selected_date.isoformat(),
                "bookings": BookingSerializer(bookings, many=True, context={"request": request}).data,
                "employees": employee_payload,
            }
        )
