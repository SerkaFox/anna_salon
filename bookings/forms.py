from datetime import timedelta
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from clients.models import Client
from employees.models import Employee
from salon.models import Zone
from services_app.models import Service

from .models import Booking
from .utils import fits_employee_schedule


REFERRAL_DISCOUNT_PERCENT = Decimal("20.00")
REFERRAL_REWARD_STEP = 5


def get_successful_referrals_count(client):
    return Client.objects.filter(
        referred_by=client,
        bookings__status=Booking.Statuses.DONE,
    ).distinct().count()


def get_available_rewards(client):
    successful_count = get_successful_referrals_count(client)
    return max((successful_count // REFERRAL_REWARD_STEP) - client.referral_rewards_used, 0)


class BookingForm(forms.ModelForm):
    apply_referral_reward = forms.BooleanField(
        required=False,
        label="Aplicar premio de referido",
    )

    class Meta:
        model = Booking
        fields = [
            "client",
            "employee",
            "service",
            "zone",
            "start_at",
            "end_at",
            "status",
            "notes",
        ]
        widgets = {
            "client": forms.Select(attrs={"class": "input"}),
            "employee": forms.Select(attrs={"class": "input"}),
            "service": forms.Select(attrs={"class": "input"}),
            "zone": forms.Select(attrs={"class": "input"}),
            "start_at": forms.DateTimeInput(
                attrs={"class": "input", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "end_at": forms.DateTimeInput(
                attrs={"class": "input", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "status": forms.Select(attrs={"class": "input"}),
            "notes": forms.Textarea(attrs={"class": "textarea", "rows": 5, "placeholder": "Notas internas"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["zone"].required = False
        self.fields["service"].queryset = Service.objects.filter(is_active=True).order_by("name")
        self.fields["employee"].queryset = Employee.objects.filter(is_active=True).order_by("first_name", "last_name")
        self.fields["zone"].queryset = Zone.objects.filter(is_active=True).order_by("name")
        self.fields["start_at"].input_formats = ("%Y-%m-%dT%H:%M",)
        self.fields["end_at"].input_formats = ("%Y-%m-%dT%H:%M",)

        service = None
        client = None

        if self.is_bound:
            service_id = self.data.get("service")
            client_id = self.data.get("client")

            if service_id:
                try:
                    service = Service.objects.prefetch_related("allowed_zones", "employees").get(pk=service_id, is_active=True)
                except Service.DoesNotExist:
                    service = None

            if client_id:
                try:
                    client = Client.objects.get(pk=client_id)
                except Client.DoesNotExist:
                    client = None

        elif self.instance.pk:
            service = self.instance.service
            client = self.instance.client

        if service:
            self.fields["employee"].queryset = service.employees.filter(is_active=True).order_by("first_name", "last_name")

            if service.requires_zone:
                self.fields["zone"].queryset = service.allowed_zones.filter(is_active=True).order_by("name")
            else:
                self.fields["zone"].queryset = Zone.objects.none()

        if self.instance.pk and self.instance.referral_reward_applied:
            self.fields["apply_referral_reward"].initial = True
            self.fields["apply_referral_reward"].disabled = True
            self.fields["apply_referral_reward"].help_text = "Este premio ya fue aplicado a esta reserva."
        elif client:
            available_rewards = get_available_rewards(client)
            if available_rewards > 0:
                self.fields["apply_referral_reward"].help_text = (
                    f"Premios disponibles: {available_rewards}. "
                    f"Descuento: {REFERRAL_DISCOUNT_PERCENT}%."
                )
            else:
                self.fields["apply_referral_reward"].help_text = "Este cliente no tiene premios disponibles."

    def clean(self):
        cleaned_data = super().clean()

        client = cleaned_data.get("client")
        employee = cleaned_data.get("employee")
        service = cleaned_data.get("service")
        zone = cleaned_data.get("zone")
        start_at = cleaned_data.get("start_at")
        end_at = cleaned_data.get("end_at")
        apply_referral_reward = cleaned_data.get("apply_referral_reward")

        if service and start_at and not end_at:
            end_at = start_at + timedelta(minutes=service.duration_minutes)
            cleaned_data["end_at"] = end_at
            self.cleaned_data["end_at"] = end_at

        if start_at and end_at and end_at <= start_at:
            self.add_error("end_at", "La fecha/hora de fin debe ser posterior al inicio.")

        if start_at and end_at:
            local_start = start_at
            local_end = end_at

            if timezone.is_naive(local_start):
                local_start = timezone.make_aware(local_start)
            if timezone.is_naive(local_end):
                local_end = timezone.make_aware(local_end)

            local_start = timezone.localtime(local_start)
            local_end = timezone.localtime(local_end)

            if local_start.date() != local_end.date():
                raise ValidationError("La reserva debe empezar y terminar el mismo día.")

        if employee and start_at and end_at:
            fits_schedule, schedule_message = fits_employee_schedule(employee, start_at, end_at)
            if not fits_schedule:
                raise ValidationError(schedule_message)

        if employee and service and not employee.services.filter(pk=service.pk).exists():
            self.add_error("employee", "Este empleado no realiza el servicio seleccionado.")

        if service:
            if service.requires_zone:
                if not zone:
                    self.add_error("zone", "Este servicio requiere una zona.")
                elif not service.allowed_zones.filter(pk=zone.pk).exists():
                    self.add_error("zone", "La zona seleccionada no está permitida para este servicio.")
            else:
                cleaned_data["zone"] = None

        if employee and start_at and end_at:
            employee_overlap = Booking.objects.filter(
                employee=employee,
                start_at__lt=end_at,
                end_at__gt=start_at,
            )
            if self.instance.pk:
                employee_overlap = employee_overlap.exclude(pk=self.instance.pk)

            if employee_overlap.exists():
                raise ValidationError("El empleado ya tiene una reserva en ese horario.")

        if zone and start_at and end_at:
            zone_overlap = Booking.objects.filter(
                zone=zone,
                start_at__lt=end_at,
                end_at__gt=start_at,
            )
            if self.instance.pk:
                zone_overlap = zone_overlap.exclude(pk=self.instance.pk)

            if zone_overlap.exists():
                raise ValidationError("La zona ya está ocupada en ese horario.")

        if apply_referral_reward and client and not (self.instance.pk and self.instance.referral_reward_applied):
            available_rewards = get_available_rewards(client)
            if available_rewards <= 0:
                self.add_error("apply_referral_reward", "Este cliente no tiene premios disponibles.")

        return cleaned_data

    def save(self, commit=True):
        booking = super().save(commit=False)

        previous_reward_applied = False
        if self.instance.pk:
            previous_reward_applied = Booking.objects.get(pk=self.instance.pk).referral_reward_applied

        should_apply_reward = self.cleaned_data.get("apply_referral_reward", False)

        if booking.service_id:
            original_price = booking.service.price or Decimal("0.00")
            booking.price_snapshot = original_price
            booking.original_client_price_snapshot = original_price
            booking.duration_snapshot = booking.service.duration_minutes

            discount_amount = Decimal("0.00")
            reward_applied = previous_reward_applied

            if previous_reward_applied:
                discount_amount = (original_price * REFERRAL_DISCOUNT_PERCENT) / Decimal("100")
                reward_applied = True
            elif should_apply_reward:
                discount_amount = (original_price * REFERRAL_DISCOUNT_PERCENT) / Decimal("100")
                reward_applied = True

            client_price = original_price - discount_amount

            employee_percent = getattr(booking.employee, "commission_percent", Decimal("40.00")) or Decimal("40.00")
            employee_amount = (client_price * employee_percent) / Decimal("100")
            salon_amount = client_price - employee_amount

            booking.discount_amount_snapshot = discount_amount
            booking.referral_reward_applied = reward_applied
            booking.client_price_snapshot = client_price
            booking.employee_percent_snapshot = employee_percent
            booking.employee_amount_snapshot = employee_amount
            booking.salon_amount_snapshot = salon_amount

            if booking.start_at and not booking.end_at:
                booking.end_at = booking.start_at + timedelta(minutes=booking.service.duration_minutes)

        if not booking.service.requires_zone:
            booking.zone = None

        if commit:
            booking.save()
            self.save_m2m()

            if booking.referral_reward_applied and not previous_reward_applied:
                client = booking.client
                client.referral_rewards_used += 1
                client.save(update_fields=["referral_rewards_used"])

        return booking
