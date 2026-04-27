from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from accounts.permissions import can_access_employee, get_employee_profile, is_admin_user
from bookings.forms import BookingForm
from bookings.models import Booking
from bookings.utils import fits_employee_schedule, is_slot_available
from clients.models import Client
from employees.models import Employee, EmployeeTimeBlock
from salon.models import Zone
from services_app.models import Service


def _format_local_datetime(value):
    if not value:
        return ""
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return value.strftime("%Y-%m-%dT%H:%M")


def _form_errors_to_validation_error(form):
    errors = {}
    for field, field_errors in form.errors.items():
        key = "non_field_errors" if field == "__all__" else field
        errors[key] = [str(error) for error in field_errors]
    if form.non_field_errors() and "non_field_errors" not in errors:
        errors["non_field_errors"] = [str(error) for error in form.non_field_errors()]
    return serializers.ValidationError(errors or {"non_field_errors": ["Datos de reserva inválidos."]})


class ClientSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = Client
        fields = ["id", "first_name", "last_name", "full_name", "phone", "email", "birth_date", "is_active"]


class EmployeeSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    service_ids = serializers.PrimaryKeyRelatedField(source="services", many=True, read_only=True)

    class Meta:
        model = Employee
        fields = ["id", "first_name", "last_name", "full_name", "phone", "email", "calendar_color", "service_ids", "is_active"]


class ServiceSerializer(serializers.ModelSerializer):
    allowed_zone_ids = serializers.PrimaryKeyRelatedField(source="allowed_zones", many=True, read_only=True)
    employee_ids = serializers.PrimaryKeyRelatedField(source="employees", many=True, read_only=True)

    class Meta:
        model = Service
        fields = ["id", "name", "description", "duration_minutes", "price", "requires_zone", "allowed_zone_ids", "employee_ids", "is_active"]


class ZoneSerializer(serializers.ModelSerializer):
    zone_type_label = serializers.CharField(source="get_zone_type_display", read_only=True)

    class Meta:
        model = Zone
        fields = ["id", "name", "zone_type", "zone_type_label", "capacity", "color", "is_active"]


class BookingSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.full_name", read_only=True)
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    service_name = serializers.CharField(source="service.name", read_only=True)
    zone_name = serializers.CharField(source="zone.name", read_only=True, allow_null=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    source_label = serializers.CharField(source="get_source_display", read_only=True)

    class Meta:
        model = Booking
        fields = [
            "id",
            "client",
            "client_name",
            "employee",
            "employee_name",
            "service",
            "service_name",
            "zone",
            "zone_name",
            "start_at",
            "end_at",
            "status",
            "status_label",
            "source",
            "source_label",
            "notes",
            "price_snapshot",
            "duration_snapshot",
            "client_price_snapshot",
            "discount_amount_snapshot",
            "employee_percent_snapshot",
            "employee_amount_snapshot",
            "salon_amount_snapshot",
            "created_at",
            "updated_at",
        ]


class BookingWriteSerializer(serializers.Serializer):
    client = serializers.PrimaryKeyRelatedField(queryset=Client.objects.filter(is_active=True), required=False)
    employee = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.filter(is_active=True), required=False)
    service = serializers.PrimaryKeyRelatedField(queryset=Service.objects.filter(is_active=True), required=False)
    zone = serializers.PrimaryKeyRelatedField(queryset=Zone.objects.filter(is_active=True), allow_null=True, required=False)
    start_at = serializers.DateTimeField(required=False)
    end_at = serializers.DateTimeField(required=False, allow_null=True)
    status = serializers.ChoiceField(choices=Booking.Statuses.choices, required=False)
    source = serializers.ChoiceField(choices=Booking.Sources.choices, required=False)
    notes = serializers.CharField(required=False, allow_blank=True)
    apply_referral_reward = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        request = self.context["request"]
        user = request.user
        instance = self.instance
        employee_profile = get_employee_profile(user)

        if not is_admin_user(user) and not employee_profile:
            raise serializers.ValidationError({"employee": ["Tu usuario no tiene empleado vinculado."]})

        values = {}
        for field in ("client", "employee", "service", "zone", "start_at", "end_at", "status", "source", "notes"):
            if field in attrs:
                values[field] = attrs[field]
            elif instance is not None:
                values[field] = getattr(instance, field)
            else:
                values[field] = None

        if not is_admin_user(user):
            requested_employee = values.get("employee") or employee_profile
            if requested_employee and not can_access_employee(user, requested_employee):
                raise serializers.ValidationError({"employee": ["Sin acceso a este empleado."]})
            values["employee"] = employee_profile

        missing = []
        for field in ("client", "employee", "service", "start_at"):
            if not values.get(field):
                missing.append(field)
        if missing:
            return_errors = {field: ["Este campo es obligatorio."] for field in missing}
            raise serializers.ValidationError(return_errors)

        if not values.get("status"):
            values["status"] = Booking.Statuses.CONFIRMED
        if not values.get("source"):
            values["source"] = Booking.Sources.MANUAL
        if values.get("notes") is None:
            values["notes"] = ""

        service = values["service"]
        start_at = values["start_at"]
        if not values.get("end_at") or "start_at" in attrs or "service" in attrs:
            values["end_at"] = start_at + timedelta(minutes=service.duration_minutes)

        form_data = {
            "client": values["client"].pk,
            "employee": values["employee"].pk,
            "service": service.pk,
            "zone": values["zone"].pk if values.get("zone") else "",
            "start_at": _format_local_datetime(values["start_at"]),
            "end_at": _format_local_datetime(values["end_at"]),
            "status": values["status"],
            "source": values["source"],
            "notes": values["notes"],
            "apply_referral_reward": "on" if attrs.get("apply_referral_reward") else "",
        }

        form = BookingForm(
            data=form_data,
            instance=instance,
            allowed_employee=None if is_admin_user(user) else employee_profile,
            allowed_clients=Client.objects.filter(is_active=True).order_by("first_name", "last_name"),
        )
        if not form.is_valid():
            raise _form_errors_to_validation_error(form)

        self._booking_form = form
        return attrs

    def save(self, **kwargs):
        return self._booking_form.save()


class AvailabilityCheckSerializer(serializers.Serializer):
    employee = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.filter(is_active=True))
    service = serializers.PrimaryKeyRelatedField(queryset=Service.objects.filter(is_active=True))
    zone = serializers.PrimaryKeyRelatedField(queryset=Zone.objects.filter(is_active=True), allow_null=True, required=False)
    start_at = serializers.DateTimeField()
    exclude_booking_id = serializers.IntegerField(required=False, min_value=1)

    def validate(self, attrs):
        request = self.context["request"]
        user = request.user
        employee = attrs["employee"]
        service = attrs["service"]
        zone = attrs.get("zone")
        start_at = attrs["start_at"]
        end_at = start_at + timedelta(minutes=service.duration_minutes)
        exclude_booking_id = attrs.get("exclude_booking_id")

        if not can_access_employee(user, employee):
            raise serializers.ValidationError({"employee": ["Sin acceso a este empleado."]})

        if not employee.services.filter(pk=service.pk).exists():
            raise serializers.ValidationError({"employee": ["Este empleado no realiza el servicio seleccionado."]})

        if service.requires_zone:
            if not zone:
                raise serializers.ValidationError({"zone": ["Este servicio requiere una zona."]})
            if not service.allowed_zones.filter(pk=zone.pk).exists():
                raise serializers.ValidationError({"zone": ["La zona seleccionada no está permitida para este servicio."]})
        else:
            zone = None

        fits_schedule, schedule_message = fits_employee_schedule(employee, start_at, end_at)
        if not fits_schedule:
            raise serializers.ValidationError({"non_field_errors": [schedule_message]})

        if not is_slot_available(employee, service, zone, start_at, end_at, exclude_booking_id=exclude_booking_id):
            raise serializers.ValidationError({"non_field_errors": ["Ese horario no está disponible para el empleado o la zona."]})

        attrs["zone"] = zone
        attrs["end_at"] = end_at
        return attrs


class BookingStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Booking.Statuses.choices)


class TimeBlockSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeTimeBlock
        fields = ["id", "employee", "date", "start_time", "end_time", "label", "color"]
