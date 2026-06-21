from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import serializers

from accounts.permissions import can_access_booking, can_access_employee, get_client_profile, get_employee_profile, is_admin_user, scope_clients_queryset
from bookings.client_actions import booking_amount_due, booking_refundable_until, can_client_cancel, can_client_reschedule
from bookings.forms import BookingForm
from bookings.models import Booking
from bookings.utils import combine_local, find_available_zone, fits_employee_schedule, is_slot_available, recurring_time_block_conflicts, time_block_conflicts
from clients.models import Client, ClientRewardRule
from clients.rewards import client_reward_progress
from documents.models import CashClosure, FiscalDocument, FiscalDocumentLine, Payment as ManualPayment
from employees.models import (
    Employee,
    EmployeeRecurringTimeBlock,
    EmployeeScheduleOverride,
    EmployeeTimeBlock,
    EmployeeWeeklyShift,
)
from payments.models import Payment as OnlinePayment
from salon.models import Zone
from services_app.models import Service

User = get_user_model()


def _can_schedule_for_employee(user, employee):
    if is_admin_user(user):
        return True
    if get_client_profile(user):
        return bool(employee)
    return bool(get_employee_profile(user) and employee)


def _format_local_datetime(value):
    if not value:
        return ""
    if timezone.is_aware(value):
        value = timezone.localtime(value, timezone.get_default_timezone())
    return value.strftime("%Y-%m-%dT%H:%M")


class SalonDateTimeField(serializers.DateTimeField):
    def enforce_timezone(self, value):
        salon_timezone = timezone.get_default_timezone()
        if timezone.is_aware(value):
            return value.astimezone(salon_timezone)
        return timezone.make_aware(value, salon_timezone)


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
    referred_by_name = serializers.CharField(source="referred_by.full_name", read_only=True, allow_null=True)
    username = serializers.CharField(source="user.username", read_only=True, allow_null=True)
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = Client
        fields = [
            "id",
            "first_name",
            "last_name",
            "full_name",
            "phone",
            "email",
            "birth_date",
            "notes",
            "is_active",
            "created_at",
            "updated_at",
            "referred_by",
            "referred_by_name",
            "referral_rewards_used",
            "username",
            "avatar_url",
        ]

    def get_avatar_url(self, obj):
        if not obj.avatar:
            return None
        return f"/api/v1/clients/{obj.pk}/avatar/"


class ClientRewardRuleSerializer(serializers.ModelSerializer):
    reward_type_label = serializers.CharField(source="get_reward_type_display", read_only=True)

    class Meta:
        model = ClientRewardRule
        fields = [
            "id",
            "name",
            "reward_type",
            "reward_type_label",
            "threshold",
            "discount_percent",
            "icon",
            "color",
            "is_active",
            "sort_order",
        ]


class ClientRewardRuleWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClientRewardRule
        fields = ["name", "threshold", "discount_percent", "icon", "color", "is_active", "sort_order"]

    def validate(self, attrs):
        request = self.context["request"]
        if not request.user.can_manage_staff:
            raise serializers.ValidationError({"non_field_errors": ["Sin permiso para editar premios."]})
        return attrs


class ClientWriteSerializer(serializers.ModelSerializer):
    referred_by = serializers.PrimaryKeyRelatedField(queryset=Client.objects.filter(is_active=True), allow_null=True, required=False)
    username = serializers.CharField(required=False, allow_blank=True, write_only=True)
    password = serializers.CharField(required=False, allow_blank=True, write_only=True, min_length=4)

    class Meta:
        model = Client
        fields = [
            "first_name",
            "last_name",
            "phone",
            "email",
            "birth_date",
            "notes",
            "referred_by",
            "username",
            "password",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request and not is_admin_user(request.user):
            self.fields["referred_by"].queryset = scope_clients_queryset(
                Client.objects.filter(is_active=True),
                request.user,
            )
            self.fields.pop("username", None)
            self.fields.pop("password", None)

    def validate(self, attrs):
        request = self.context.get("request")
        if request and is_admin_user(request.user):
            username = (attrs.get("username") or "").strip()
            if username:
                exists = User.objects.filter(username=username)
                if self.instance and self.instance.user_id:
                    exists = exists.exclude(pk=self.instance.user_id)
                if exists.exists():
                    raise serializers.ValidationError({"username": ["Este usuario ya existe."]})
        return attrs

    def create(self, validated_data):
        username = validated_data.pop("username", "")
        password = validated_data.pop("password", "")
        client = super().create(validated_data)
        self._sync_user(client, username, password)
        return client

    def update(self, instance, validated_data):
        username = validated_data.pop("username", "")
        password = validated_data.pop("password", "")
        client = super().update(instance, validated_data)
        self._sync_user(client, username, password)
        return client

    def _sync_user(self, client, username, password):
        request = self.context.get("request")
        if not request or not is_admin_user(request.user):
            return
        username = (username or "").strip()
        if not username and not password:
            return
        user = client.user
        if user is None:
            user = User(username=username, role=User.ROLE_CLIENT)
        if username:
            user.username = username
        user.first_name = client.first_name
        user.last_name = client.last_name
        user.email = client.email
        user.phone = client.phone
        user.role = User.ROLE_CLIENT
        user.is_active = True
        if password:
            user.set_password(password)
        user.save()
        if client.user_id != user.pk:
            client.user = user
            client.save(update_fields=["user"])


class EmployeeSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    service_ids = serializers.PrimaryKeyRelatedField(source="services", many=True, read_only=True)
    service_names = serializers.SerializerMethodField()
    username = serializers.CharField(source="user.username", read_only=True, allow_null=True)

    class Meta:
        model = Employee
        fields = [
            "id",
            "first_name",
            "last_name",
            "full_name",
            "phone",
            "email",
            "calendar_color",
            "commission_percent",
            "service_ids",
            "service_names",
            "username",
            "notes",
            "is_active",
            "created_at",
            "updated_at",
        ]

    def get_service_names(self, obj):
        return [service.name for service in obj.services.all()]


class EmployeeWriteSerializer(serializers.ModelSerializer):
    username = serializers.CharField(
        required=False,
        allow_blank=True,
        write_only=True,
    )
    password = serializers.CharField(
        required=False,
        allow_blank=True,
        write_only=True,
        min_length=4,
    )
    services = serializers.PrimaryKeyRelatedField(
        queryset=Service.objects.filter(is_active=True),
        many=True,
        required=False,
    )

    class Meta:
        model = Employee
        fields = [
            "first_name",
            "last_name",
            "phone",
            "email",
            "services",
            "calendar_color",
            "commission_percent",
            "is_active",
            "notes",
            "username",
            "password",
        ]

    def validate(self, attrs):
        request = self.context["request"]
        if request.user.can_manage_staff:
            username = (attrs.get("username") or "").strip()
            password = attrs.get("password") or ""
            current_user = self.instance.user if self.instance else None
            if username:
                exists = User.objects.filter(username=username)
                if current_user:
                    exists = exists.exclude(pk=current_user.pk)
                existing_user = exists.first()
                linked_employee = (
                    getattr(existing_user, "employee_profile", None)
                    if existing_user
                    else None
                )
                if linked_employee and linked_employee != self.instance:
                    raise serializers.ValidationError(
                        {"username": ["Este usuario ya esta vinculado a otro empleado."]}
                    )
                if not current_user and not password:
                    raise serializers.ValidationError(
                        {"password": ["Introduce una contraseña inicial."]}
                    )
            elif password and not current_user:
                raise serializers.ValidationError(
                    {"username": ["Introduce un usuario para crear el acceso."]}
                )
            return attrs
        if self.instance is None:
            raise serializers.ValidationError(
                {"non_field_errors": ["Sin permiso para crear empleados."]}
            )
        if not can_access_employee(request.user, self.instance):
            raise serializers.ValidationError(
                {"non_field_errors": ["Sin permiso para editar este empleado."]}
            )
        allowed_fields = {
            "first_name",
            "last_name",
            "phone",
            "email",
            "calendar_color",
            "services",
        }
        blocked_fields = set(attrs) - allowed_fields
        if blocked_fields:
            raise serializers.ValidationError(
                {
                    "non_field_errors": [
                        "Solo puedes editar tus datos de contacto, color y servicios."
                    ]
                }
            )
        return attrs

    def create(self, validated_data):
        username = validated_data.pop("username", "")
        password = validated_data.pop("password", "")
        employee = super().create(validated_data)
        self._sync_user(employee, username, password)
        return employee

    def update(self, instance, validated_data):
        username = validated_data.pop("username", "")
        password = validated_data.pop("password", "")
        employee = super().update(instance, validated_data)
        self._sync_user(employee, username, password)
        return employee

    def _sync_user(self, employee, username, password):
        request = self.context["request"]
        if not request.user.can_manage_staff:
            return
        username = (username or "").strip()
        password = password or ""
        if not username and not password:
            return
        user = employee.user
        if user is None:
            user = User.objects.filter(username=username).first()
            if user is None:
                user = User(username=username, role=User.ROLE_EMPLOYEE)
        if username:
            user.username = username
        user.first_name = employee.first_name
        user.last_name = employee.last_name
        user.email = employee.email
        user.role = User.ROLE_EMPLOYEE
        user.is_active = employee.is_active
        if password:
            user.set_password(password)
        user.save()
        if employee.user_id != user.pk:
            employee.user = user
            employee.save(update_fields=["user"])


class EmployeeWeeklyShiftSerializer(serializers.ModelSerializer):
    weekday_label = serializers.CharField(source="get_weekday_display", read_only=True)

    class Meta:
        model = EmployeeWeeklyShift
        fields = [
            "id",
            "weekday",
            "weekday_label",
            "is_day_off",
            "start_time",
            "end_time",
            "break_start",
            "break_end",
            "break_label",
            "note",
        ]
        read_only_fields = ["id", "weekday_label"]

    def validate(self, attrs):
        is_day_off = attrs.get("is_day_off", self.instance.is_day_off if self.instance else False)
        start_time = attrs.get("start_time", self.instance.start_time if self.instance else None)
        end_time = attrs.get("end_time", self.instance.end_time if self.instance else None)
        break_start = attrs.get("break_start", self.instance.break_start if self.instance else None)
        break_end = attrs.get("break_end", self.instance.break_end if self.instance else None)

        if is_day_off:
            return attrs
        if not start_time or not end_time:
            raise serializers.ValidationError({"start_time": ["Indica inicio y fin del turno."]})
        if end_time <= start_time:
            raise serializers.ValidationError({"end_time": ["La hora de fin debe ser posterior al inicio."]})
        if bool(break_start) != bool(break_end):
            raise serializers.ValidationError({"break_start": ["Indica inicio y fin de la pausa."]})
        if break_start and break_end:
            if break_end <= break_start:
                raise serializers.ValidationError({"break_end": ["La pausa debe terminar despues de empezar."]})
            if break_start < start_time or break_end > end_time:
                raise serializers.ValidationError({"break_start": ["La pausa debe estar dentro del turno."]})
        return attrs


class EmployeeScheduleOverrideSerializer(serializers.ModelSerializer):
    delete = serializers.BooleanField(write_only=True, required=False, default=False)

    class Meta:
        model = EmployeeScheduleOverride
        fields = [
            "id",
            "date",
            "is_day_off",
            "start_time",
            "end_time",
            "break_start",
            "break_end",
            "break_label",
            "label",
            "delete",
        ]
        read_only_fields = ["id"]

    def validate(self, attrs):
        if attrs.get("delete"):
            return attrs
        is_day_off = attrs.get("is_day_off", self.instance.is_day_off if self.instance else False)
        start_time = attrs.get("start_time", self.instance.start_time if self.instance else None)
        end_time = attrs.get("end_time", self.instance.end_time if self.instance else None)
        break_start = attrs.get("break_start", self.instance.break_start if self.instance else None)
        break_end = attrs.get("break_end", self.instance.break_end if self.instance else None)

        if is_day_off:
            return attrs
        if not start_time or not end_time:
            raise serializers.ValidationError({"start_time": ["Indica inicio y fin del turno especial."]})
        if end_time <= start_time:
            raise serializers.ValidationError({"end_time": ["La hora de fin debe ser posterior al inicio."]})
        if bool(break_start) != bool(break_end):
            raise serializers.ValidationError({"break_start": ["Indica inicio y fin de la pausa."]})
        if break_start and break_end:
            if break_end <= break_start:
                raise serializers.ValidationError({"break_end": ["La pausa debe terminar despues de empezar."]})
            if break_start < start_time or break_end > end_time:
                raise serializers.ValidationError({"break_start": ["La pausa debe estar dentro del turno."]})
        return attrs


class EmployeeScheduleSerializer(serializers.Serializer):
    weekly_shifts = EmployeeWeeklyShiftSerializer(many=True, required=False)
    overrides = EmployeeScheduleOverrideSerializer(many=True, required=False)

    def validate(self, attrs):
        request = self.context["request"]
        if not request.user.can_manage_staff:
            raise serializers.ValidationError({"non_field_errors": ["Sin permiso para editar horarios."]})
        return attrs

    def update(self, instance, validated_data):
        weekly_shifts = validated_data.get("weekly_shifts")
        if weekly_shifts is not None:
            by_weekday = {item["weekday"]: item for item in weekly_shifts}
            for weekday in range(7):
                shift, _created = EmployeeWeeklyShift.objects.get_or_create(
                    employee=instance,
                    weekday=weekday,
                )
                if weekday not in by_weekday:
                    continue
                data = by_weekday[weekday]
                for field in (
                    "is_day_off",
                    "start_time",
                    "end_time",
                    "break_start",
                    "break_end",
                    "break_label",
                    "note",
                ):
                    if field in data:
                        setattr(shift, field, data[field])
                if shift.is_day_off:
                    shift.start_time = None
                    shift.end_time = None
                    shift.break_start = None
                    shift.break_end = None
                shift.save()

        overrides = validated_data.get("overrides")
        if overrides is not None:
            for data in overrides:
                date_value = data["date"]
                override = EmployeeScheduleOverride.objects.filter(
                    employee=instance,
                    date=date_value,
                ).first()
                if data.get("delete"):
                    if override:
                        override.delete()
                    continue
                if override is None:
                    override = EmployeeScheduleOverride(employee=instance, date=date_value)
                for field in (
                    "is_day_off",
                    "start_time",
                    "end_time",
                    "break_start",
                    "break_end",
                    "break_label",
                    "label",
                ):
                    if field in data:
                        setattr(override, field, data[field])
                if override.is_day_off:
                    override.start_time = None
                    override.end_time = None
                    override.break_start = None
                    override.break_end = None
                override.save()
        return instance


class ServiceSerializer(serializers.ModelSerializer):
    allowed_zone_ids = serializers.PrimaryKeyRelatedField(source="allowed_zones", many=True, read_only=True)
    employee_ids = serializers.PrimaryKeyRelatedField(source="employees", many=True, read_only=True)

    class Meta:
        model = Service
        fields = ["id", "name", "description", "duration_minutes", "price", "color", "requires_zone", "allowed_zone_ids", "employee_ids", "is_active"]


class ServiceWriteSerializer(serializers.ModelSerializer):
    allowed_zones = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
    )

    class Meta:
        model = Service
        fields = [
            "name",
            "description",
            "duration_minutes",
            "price",
            "color",
            "requires_zone",
            "allowed_zones",
            "is_active",
        ]

    def validate(self, attrs):
        request = self.context["request"]
        if not request.user.can_manage_staff:
            raise serializers.ValidationError({"non_field_errors": ["Sin permiso para editar servicios."]})
        requires_zone = attrs.get("requires_zone", self.instance.requires_zone if self.instance else False)
        if "allowed_zones" in attrs:
            zone_ids = list(dict.fromkeys(attrs["allowed_zones"]))
            zones = list(Zone.objects.filter(pk__in=zone_ids, is_active=True))
            attrs["allowed_zones"] = zones
            has_allowed_zones = bool(zones)
        elif self.instance:
            has_allowed_zones = self.instance.allowed_zones.filter(is_active=True).exists()
        else:
            has_allowed_zones = False
        if requires_zone and not has_allowed_zones:
            raise serializers.ValidationError({"allowed_zones": ["Selecciona al menos una zona para este servicio."]})
        if not requires_zone:
            attrs["allowed_zones"] = []
        return attrs


class ZoneSerializer(serializers.ModelSerializer):
    zone_type_label = serializers.CharField(source="get_zone_type_display", read_only=True)

    class Meta:
        model = Zone
        fields = ["id", "name", "zone_type", "zone_type_label", "capacity", "color", "notes", "is_active"]


class ZoneWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Zone
        fields = ["name", "zone_type", "capacity", "color", "notes", "is_active"]

    def validate(self, attrs):
        request = self.context["request"]
        if not request.user.can_manage_staff:
            raise serializers.ValidationError({"non_field_errors": ["Sin permiso para editar zonas."]})
        return attrs


class BookingSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.full_name", read_only=True)
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    service_name = serializers.CharField(source="service.name", read_only=True)
    zone_name = serializers.CharField(source="zone.name", read_only=True, allow_null=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    source_label = serializers.CharField(source="get_source_display", read_only=True)
    start_at = serializers.SerializerMethodField()
    end_at = serializers.SerializerMethodField()
    reward_rule_name = serializers.CharField(source="reward_rule.name", read_only=True, allow_null=True)
    online_payment_status = serializers.SerializerMethodField()
    online_payment_paid_total = serializers.SerializerMethodField()
    online_payment_remaining_amount = serializers.SerializerMethodField()
    payment_status = serializers.SerializerMethodField()
    paid_amount = serializers.SerializerMethodField()
    latest_payment_id = serializers.SerializerMethodField()
    can_pay = serializers.SerializerMethodField()
    amount_due = serializers.SerializerMethodField()
    can_cancel = serializers.SerializerMethodField()
    can_refund = serializers.SerializerMethodField()
    refundable_until = serializers.SerializerMethodField()
    can_reschedule = serializers.SerializerMethodField()

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
            "reward_rule",
            "reward_rule_name",
            "online_payment_status",
            "online_payment_paid_total",
            "online_payment_remaining_amount",
            "payment_status",
            "paid_amount",
            "latest_payment_id",
            "can_pay",
            "amount_due",
            "can_cancel",
            "can_refund",
            "refundable_until",
            "can_reschedule",
            "employee_percent_snapshot",
            "employee_amount_snapshot",
            "salon_amount_snapshot",
            "created_at",
            "updated_at",
        ]

    def get_start_at(self, obj):
        return _format_local_datetime(obj.start_at)

    def get_end_at(self, obj):
        return _format_local_datetime(obj.end_at)

    def _payment_info(self, obj):
        if hasattr(obj, "_mobile_online_payment_info"):
            return obj._mobile_online_payment_info
        payments = list(getattr(obj, "_prefetched_objects_cache", {}).get("online_payments", obj.online_payments.all()))
        paid_total = sum((payment.amount for payment in payments if payment.status == OnlinePayment.Statuses.PAID), 0)
        total_amount = obj.client_price_snapshot or obj.price_snapshot or 0
        latest_payment = payments[0] if payments else None
        obj._mobile_online_payment_info = {
            "status": latest_payment.status if latest_payment else "",
            "paid_total": paid_total,
            "remaining_amount": max(total_amount - paid_total, 0),
            "latest_payment_id": latest_payment.pk if latest_payment else None,
            "can_pay": total_amount > paid_total and obj.status not in {Booking.Statuses.CANCELLED, Booking.Statuses.NO_SHOW},
        }
        return obj._mobile_online_payment_info

    def get_online_payment_status(self, obj):
        return self._payment_info(obj)["status"]

    def get_online_payment_paid_total(self, obj):
        return str(self._payment_info(obj)["paid_total"])

    def get_online_payment_remaining_amount(self, obj):
        return str(self._payment_info(obj)["remaining_amount"])

    def get_payment_status(self, obj):
        return self._payment_info(obj)["status"]

    def get_paid_amount(self, obj):
        return str(self._payment_info(obj)["paid_total"])

    def get_latest_payment_id(self, obj):
        return self._payment_info(obj)["latest_payment_id"]

    def get_can_pay(self, obj):
        return self._payment_info(obj)["can_pay"]

    def get_amount_due(self, obj):
        return str(booking_amount_due(obj))

    def get_can_cancel(self, obj):
        return can_client_cancel(obj)

    def get_can_refund(self, obj):
        return timezone.now() <= booking_refundable_until(obj) and self._payment_info(obj)["paid_total"] > 0

    def get_refundable_until(self, obj):
        return _format_local_datetime(booking_refundable_until(obj))

    def get_can_reschedule(self, obj):
        return can_client_reschedule(obj)


class BookingWriteSerializer(serializers.Serializer):
    client = serializers.PrimaryKeyRelatedField(queryset=Client.objects.filter(is_active=True), required=False)
    employee = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.filter(is_active=True), required=False)
    service = serializers.PrimaryKeyRelatedField(queryset=Service.objects.filter(is_active=True), required=False)
    zone = serializers.PrimaryKeyRelatedField(queryset=Zone.objects.filter(is_active=True), allow_null=True, required=False)
    start_at = SalonDateTimeField(required=False)
    end_at = SalonDateTimeField(required=False, allow_null=True)
    status = serializers.ChoiceField(choices=Booking.Statuses.choices, required=False)
    source = serializers.ChoiceField(choices=Booking.Sources.choices, required=False)
    notes = serializers.CharField(required=False, allow_blank=True)
    apply_referral_reward = serializers.BooleanField(required=False, default=False)
    reward_rule = serializers.PrimaryKeyRelatedField(queryset=ClientRewardRule.objects.filter(is_active=True), allow_null=True, required=False)

    def validate(self, attrs):
        request = self.context["request"]
        user = request.user
        instance = self.instance
        employee_profile = get_employee_profile(user)
        client_profile = get_client_profile(user)

        if not is_admin_user(user) and not employee_profile and not client_profile:
            raise serializers.ValidationError({"employee": ["Tu usuario no tiene empleado vinculado."]})

        values = {}
        for field in ("client", "employee", "service", "zone", "start_at", "end_at", "status", "source", "notes", "reward_rule"):
            if field in attrs:
                values[field] = attrs[field]
            elif instance is not None:
                values[field] = getattr(instance, field)
            else:
                values[field] = None

        if client_profile:
            values["client"] = client_profile
            values["status"] = Booking.Statuses.PENDING
            values["source"] = Booking.Sources.WEBSITE
        elif not is_admin_user(user):
            requested_employee = values.get("employee") or employee_profile
            if requested_employee and not _can_schedule_for_employee(user, requested_employee):
                raise serializers.ValidationError({"employee": ["Sin acceso a este empleado."]})
            values["employee"] = requested_employee

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
        employee = values["employee"]
        if not employee.services.filter(pk=service.pk).exists():
            raise serializers.ValidationError({"employee": ["Este empleado no realiza el servicio seleccionado."]})

        start_at = values["start_at"]
        if not values.get("end_at") or "start_at" in attrs or "service" in attrs:
            values["end_at"] = start_at + timedelta(minutes=service.duration_minutes)

        if service.requires_zone and values.get("zone") is None:
            values["zone"] = find_available_zone(
                service,
                values["start_at"],
                values["end_at"],
                exclude_booking_id=instance.pk if instance else None,
            )
            if values["zone"] is None:
                raise serializers.ValidationError(
                    {"zone": ["No hay zona libre para este horario."]}
                )

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
            "reward_rule": values["reward_rule"].pk if values.get("reward_rule") else "",
        }

        form = BookingForm(
            data=form_data,
            instance=instance,
            allowed_employee=None,
            allowed_clients=Client.objects.filter(pk=client_profile.pk) if client_profile else Client.objects.filter(is_active=True).order_by("first_name", "last_name"),
        )
        if not form.is_valid():
            raise _form_errors_to_validation_error(form)

        self._booking_form = form
        return attrs


class FiscalDocumentLineSerializer(serializers.ModelSerializer):
    total_amount = serializers.SerializerMethodField()
    service_name = serializers.CharField(source="service.name", read_only=True, allow_null=True)

    class Meta:
        model = FiscalDocumentLine
        fields = [
            "id",
            "service",
            "service_name",
            "description",
            "quantity",
            "unit_amount",
            "total_amount",
            "sort_order",
        ]

    def get_total_amount(self, obj):
        return str(obj.total_amount)


class ManualPaymentSerializer(serializers.ModelSerializer):
    method_label = serializers.CharField(source="get_method_display", read_only=True)
    entry_type_label = serializers.CharField(source="get_entry_type_display", read_only=True)
    paid_at = serializers.SerializerMethodField()
    signed_amount = serializers.SerializerMethodField()

    class Meta:
        model = ManualPayment
        fields = [
            "id",
            "fiscal_document",
            "booking",
            "entry_type",
            "entry_type_label",
            "paid_at",
            "amount",
            "signed_amount",
            "method",
            "method_label",
            "reference",
            "notes",
        ]

    def get_paid_at(self, obj):
        return _format_local_datetime(obj.paid_at)

    def get_signed_amount(self, obj):
        return str(obj.signed_amount)


class FiscalDocumentSerializer(serializers.ModelSerializer):
    document_type_label = serializers.CharField(source="get_document_type_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    client_name = serializers.CharField(source="booking.client.full_name", read_only=True)
    service_name = serializers.CharField(source="booking.service.name", read_only=True)
    booking_start_at = serializers.SerializerMethodField()
    payments_total = serializers.SerializerMethodField()
    balance_due = serializers.SerializerMethodField()
    is_paid = serializers.BooleanField(read_only=True)
    lines = FiscalDocumentLineSerializer(many=True, read_only=True)
    payments = ManualPaymentSerializer(many=True, read_only=True)

    class Meta:
        model = FiscalDocument
        fields = [
            "id",
            "booking",
            "document_type",
            "document_type_label",
            "status",
            "status_label",
            "number",
            "issue_date",
            "tax_rate",
            "subtotal_amount",
            "tax_amount",
            "total_amount",
            "payments_total",
            "balance_due",
            "is_paid",
            "notes",
            "client_name",
            "service_name",
            "booking_start_at",
            "lines",
            "payments",
        ]

    def get_booking_start_at(self, obj):
        return _format_local_datetime(obj.booking.start_at)

    def get_payments_total(self, obj):
        return str(obj.payments_total)

    def get_balance_due(self, obj):
        return str(obj.balance_due)


class CashClosureSerializer(serializers.ModelSerializer):
    closed_by_name = serializers.CharField(source="closed_by.get_full_name", read_only=True, allow_null=True)
    closed_at = serializers.SerializerMethodField()

    class Meta:
        model = CashClosure
        fields = [
            "id",
            "closure_date",
            "total_amount",
            "cash_amount",
            "card_amount",
            "bizum_amount",
            "transfer_amount",
            "payments_count",
            "notes",
            "closed_by",
            "closed_by_name",
            "closed_at",
        ]

    def get_closed_at(self, obj):
        return _format_local_datetime(obj.closed_at)


class FiscalDocumentLineWriteSerializer(serializers.Serializer):
    service = serializers.PrimaryKeyRelatedField(
        queryset=Service.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )
    description = serializers.CharField(required=False, allow_blank=True)
    quantity = serializers.DecimalField(max_digits=8, decimal_places=2, required=False, min_value=Decimal("0.01"))
    unit_amount = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, min_value=Decimal("0.00"))
    manual_amount = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, min_value=Decimal("0.00"))

    def validate(self, attrs):
        service = attrs.get("service")
        description = (attrs.get("description") or "").strip()
        manual_amount = attrs.get("manual_amount")
        unit_amount = attrs.get("unit_amount")

        if service:
            attrs["description"] = description or service.name
            if unit_amount in (None, ""):
                attrs["unit_amount"] = service.price
        elif manual_amount is not None:
            attrs["unit_amount"] = manual_amount

        if not attrs.get("description"):
            raise serializers.ValidationError({"description": ["Indica un concepto."]})
        if attrs.get("unit_amount") is None:
            raise serializers.ValidationError({"unit_amount": ["Indica un importe."]})
        if "quantity" not in attrs:
            attrs["quantity"] = Decimal("1.00")
        return attrs


class ManualPaymentWriteSerializer(serializers.ModelSerializer):
    paid_at = SalonDateTimeField(required=False)

    class Meta:
        model = ManualPayment
        fields = ["paid_at", "entry_type", "amount", "method", "reference", "notes"]


class CashClosureWriteSerializer(serializers.Serializer):
    date = serializers.DateField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True)

    def save(self, **kwargs):
        return self._booking_form.save()


class AvailabilityCheckSerializer(serializers.Serializer):
    employee = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.filter(is_active=True))
    service = serializers.PrimaryKeyRelatedField(queryset=Service.objects.filter(is_active=True))
    zone = serializers.PrimaryKeyRelatedField(queryset=Zone.objects.filter(is_active=True), allow_null=True, required=False)
    start_at = SalonDateTimeField()
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

        if not _can_schedule_for_employee(user, employee):
            raise serializers.ValidationError({"employee": ["Sin acceso a este empleado."]})

        if not employee.services.filter(pk=service.pk).exists():
            raise serializers.ValidationError({"employee": ["Este empleado no realiza el servicio seleccionado."]})

        if service.requires_zone:
            if zone and not service.allowed_zones.filter(pk=zone.pk).exists():
                raise serializers.ValidationError({"zone": ["La zona seleccionada no está permitida para este servicio."]})
        else:
            zone = None

        fits_schedule, schedule_message = fits_employee_schedule(employee, start_at, end_at)
        if not fits_schedule:
            raise serializers.ValidationError({"non_field_errors": [schedule_message]})

        if not is_slot_available(employee, service, zone, start_at, end_at, exclude_booking_id=exclude_booking_id):
            raise serializers.ValidationError({"non_field_errors": ["Ese horario no está disponible para el empleado o la zona."]})

        if service.requires_zone and zone is None:
            zone = find_available_zone(service, start_at, end_at, exclude_booking_id=exclude_booking_id)
            if zone is None:
                raise serializers.ValidationError({"zone": ["No hay zona libre para este horario."]})

        attrs["zone"] = zone
        attrs["end_at"] = end_at
        return attrs


class AvailabilitySlotsQuerySerializer(serializers.Serializer):
    date = serializers.DateField(required=True, input_formats=["%Y-%m-%d"])
    employee = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.filter(is_active=True), required=False)
    service = serializers.PrimaryKeyRelatedField(queryset=Service.objects.filter(is_active=True))
    zone = serializers.PrimaryKeyRelatedField(queryset=Zone.objects.filter(is_active=True), allow_null=True, required=False)
    booking = serializers.PrimaryKeyRelatedField(queryset=Booking.objects.select_related("employee"), required=False)

    def validate(self, attrs):
        request = self.context["request"]
        employee = attrs["employee"]
        service = attrs["service"]
        zone = attrs.get("zone")
        booking = attrs.get("booking")

        if employee and not _can_schedule_for_employee(request.user, employee):
            raise serializers.ValidationError({"employee": ["Sin acceso a este empleado."]})

        if booking and not (can_access_booking(request.user, booking) or get_employee_profile(request.user)):
            raise serializers.ValidationError({"booking": ["Sin acceso a esta reserva."]})

        if employee and not employee.services.filter(pk=service.pk).exists():
            raise serializers.ValidationError({"employee": ["Este empleado no realiza el servicio seleccionado."]})

        if service.requires_zone:
            if zone and not service.allowed_zones.filter(pk=zone.pk).exists():
                raise serializers.ValidationError({"zone": ["La zona seleccionada no está permitida para este servicio."]})
        else:
            zone = None

        attrs["zone"] = zone
        return attrs


class BookingStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Booking.Statuses.choices)


class TimeBlockSerializer(serializers.Serializer):
    id = serializers.CharField(read_only=True)
    employee = serializers.IntegerField(read_only=True)
    date = serializers.DateField(read_only=True)
    start_time = serializers.TimeField(read_only=True)
    end_time = serializers.TimeField(read_only=True)
    start_at = serializers.CharField(read_only=True)
    end_at = serializers.CharField(read_only=True)
    label = serializers.CharField(read_only=True)
    reason = serializers.CharField(read_only=True)
    color = serializers.CharField(read_only=True)
    is_recurring = serializers.BooleanField(read_only=True)
    recurring_id = serializers.IntegerField(read_only=True, allow_null=True)
    editable = serializers.BooleanField(read_only=True)

    def to_representation(self, instance):
        if isinstance(instance, dict):
            data = instance
            date_value = data["date"]
            start_time = data["start_time"]
            end_time = data["end_time"]
            label = data.get("label") or "Bloqueo"
            start_at = combine_local(date_value, start_time)
            end_at = combine_local(date_value, end_time)
            return {
                "id": data["id"],
                "employee": data["employee_id"],
                "date": date_value.isoformat(),
                "start_time": start_time.strftime("%H:%M:%S"),
                "end_time": end_time.strftime("%H:%M:%S"),
                "start_at": timezone.localtime(start_at, timezone.get_default_timezone()).isoformat(),
                "end_at": timezone.localtime(end_at, timezone.get_default_timezone()).isoformat(),
                "label": label,
                "reason": label,
                "color": data.get("color") or "#111111",
                "is_recurring": data.get("is_recurring", False),
                "recurring_id": data.get("recurring_id"),
                "editable": data.get("editable", True),
            }

        label = instance.label or "Bloqueo"
        start_at = combine_local(instance.date, instance.start_time)
        end_at = combine_local(instance.date, instance.end_time)
        return {
            "id": instance.pk,
            "employee": instance.employee_id,
            "date": instance.date.isoformat(),
            "start_time": instance.start_time.strftime("%H:%M:%S"),
            "end_time": instance.end_time.strftime("%H:%M:%S"),
            "start_at": timezone.localtime(start_at, timezone.get_default_timezone()).isoformat(),
            "end_at": timezone.localtime(end_at, timezone.get_default_timezone()).isoformat(),
            "label": label,
            "reason": label,
            "color": instance.color or "#111111",
            "is_recurring": False,
            "recurring_id": None,
            "editable": True,
        }


class TimeBlockWriteSerializer(serializers.Serializer):
    employee = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.filter(is_active=True), required=False)
    start_at = SalonDateTimeField(required=False)
    end_at = SalonDateTimeField(required=False)
    reason = serializers.CharField(required=False, allow_blank=True)
    label = serializers.CharField(required=False, allow_blank=True)
    color = serializers.CharField(required=False, allow_blank=True)
    force = serializers.BooleanField(required=False, default=False)
    recurring = serializers.BooleanField(required=False, default=False)
    weekday = serializers.IntegerField(required=False, min_value=0, max_value=6)
    start_time = serializers.TimeField(required=False)
    end_time = serializers.TimeField(required=False)
    active = serializers.BooleanField(required=False, default=True)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False, allow_null=True)

    def validate(self, attrs):
        request = self.context["request"]
        instance = self.instance
        is_recurring = attrs.get("recurring", isinstance(instance, EmployeeRecurringTimeBlock))

        if isinstance(instance, EmployeeTimeBlock) and is_recurring:
            raise serializers.ValidationError({"recurring": ["No se puede convertir un bloqueo puntual en recurrente."]})

        employee = attrs.get("employee") or (instance.employee if instance is not None else None)
        if not employee:
            raise serializers.ValidationError({"employee": ["Este campo es obligatorio."]})
        if not can_access_employee(request.user, employee):
            raise serializers.ValidationError({"employee": ["Sin acceso a este empleado."]})

        label = (attrs.get("reason") or attrs.get("label") or (instance.label if instance is not None else "") or "Bloqueo").strip()
        color = (attrs.get("color") or (instance.color if instance is not None else "") or "#111111").strip()

        if is_recurring:
            return self._validate_recurring(attrs, employee, label, color)
        return self._validate_one_time(attrs, employee, label, color)

    def _validate_one_time(self, attrs, employee, label, color):
        instance = self.instance
        start_at = attrs.get("start_at")
        end_at = attrs.get("end_at")

        if instance is not None:
            if start_at is None:
                start_at = combine_local(instance.date, instance.start_time)
            if end_at is None:
                end_at = combine_local(instance.date, instance.end_time)

        if not start_at:
            raise serializers.ValidationError({"start_at": ["Este campo es obligatorio."]})
        if not end_at:
            raise serializers.ValidationError({"end_at": ["Este campo es obligatorio."]})
        if end_at <= start_at:
            raise serializers.ValidationError({"end_at": ["La hora de fin debe ser posterior al inicio."]})

        local_start = timezone.localtime(start_at, timezone.get_default_timezone())
        local_end = timezone.localtime(end_at, timezone.get_default_timezone())
        if local_start.date() != local_end.date():
            raise serializers.ValidationError({"end_at": ["El bloqueo debe empezar y terminar el mismo día."]})

        exclude_id = instance.pk if instance is not None else None
        if time_block_conflicts(employee, local_start.date(), local_start.time(), local_end.time(), exclude_time_block_id=exclude_id):
            raise serializers.ValidationError({"non_field_errors": ["El bloqueo se solapa con otro bloqueo del empleado."]})

        booking_conflict = Booking.objects.exclude(status=Booking.Statuses.CANCELLED).filter(
            employee=employee,
            start_at__lt=end_at,
            end_at__gt=start_at,
        ).exists()
        if booking_conflict and not attrs.get("force", False):
            raise serializers.ValidationError({"non_field_errors": ["El bloqueo se solapa con una reserva existente."]})

        attrs["_employee"] = employee
        attrs["_date"] = local_start.date()
        attrs["_start_time"] = local_start.time()
        attrs["_end_time"] = local_end.time()
        attrs["_label"] = label
        attrs["_color"] = color
        return attrs

    def _validate_recurring(self, attrs, employee, label, color):
        instance = self.instance
        required = {}
        for field in ("weekday", "start_time", "end_time", "date_from"):
            if field not in attrs and instance is None:
                required[field] = ["Este campo es obligatorio para un bloqueo recurrente."]
        if required:
            raise serializers.ValidationError(required)

        weekday = attrs.get("weekday", instance.weekday if instance is not None else None)
        start_time = attrs.get("start_time", instance.start_time if instance is not None else None)
        end_time = attrs.get("end_time", instance.end_time if instance is not None else None)
        date_from = attrs.get("date_from", instance.date_from if instance is not None else None)
        date_to = attrs.get("date_to", instance.date_to if instance is not None else None)

        if end_time <= start_time:
            raise serializers.ValidationError({"end_time": ["La hora de fin debe ser posterior al inicio."]})
        if date_to and date_to < date_from:
            raise serializers.ValidationError({"date_to": ["La fecha final debe ser posterior o igual a la inicial."]})

        exclude_id = instance.pk if isinstance(instance, EmployeeRecurringTimeBlock) else None
        if recurring_time_block_conflicts(employee, weekday, start_time, end_time, date_from, date_to, exclude_recurring_id=exclude_id):
            raise serializers.ValidationError({"non_field_errors": ["El bloqueo recurrente se solapa con otro bloqueo del empleado."]})

        one_time_blocks = EmployeeTimeBlock.objects.filter(
            employee=employee,
            date__gte=date_from,
            start_time__lt=end_time,
            end_time__gt=start_time,
        )
        if date_to:
            one_time_blocks = one_time_blocks.filter(date__lte=date_to)
        if any(block.date.weekday() == weekday for block in one_time_blocks):
            raise serializers.ValidationError({"non_field_errors": ["El bloqueo recurrente se solapa con otro bloqueo del empleado."]})

        attrs["_employee"] = employee
        attrs["_weekday"] = weekday
        attrs["_start_time"] = start_time
        attrs["_end_time"] = end_time
        attrs["_date_from"] = date_from
        attrs["_date_to"] = date_to
        attrs["_label"] = label
        attrs["_color"] = color
        return attrs

    def save(self, **kwargs):
        if self.validated_data.get("recurring") or isinstance(self.instance, EmployeeRecurringTimeBlock):
            instance = self.instance or EmployeeRecurringTimeBlock()
            instance.employee = self.validated_data["_employee"]
            instance.weekday = self.validated_data["_weekday"]
            instance.start_time = self.validated_data["_start_time"]
            instance.end_time = self.validated_data["_end_time"]
            instance.label = self.validated_data["_label"]
            instance.color = self.validated_data["_color"]
            instance.active = self.validated_data.get("active", instance.active if instance.pk else True)
            instance.date_from = self.validated_data["_date_from"]
            instance.date_to = self.validated_data["_date_to"]
            instance.save()
            return instance

        instance = self.instance or EmployeeTimeBlock()
        instance.employee = self.validated_data["_employee"]
        instance.date = self.validated_data["_date"]
        instance.start_time = self.validated_data["_start_time"]
        instance.end_time = self.validated_data["_end_time"]
        instance.label = self.validated_data["_label"]
        instance.color = self.validated_data["_color"]
        instance.save()
        return instance
