from django import forms
from django.contrib.auth import get_user_model
from django.forms import BaseInlineFormSet, inlineformset_factory

from services_app.models import Service

from .models import Employee, EmployeeScheduleOverride, EmployeeWeeklyShift, Weekday

User = get_user_model()


class EmployeeForm(forms.ModelForm):
    user = forms.ModelChoiceField(
        label="Usuario del sistema",
        queryset=User.objects.all().order_by("username"),
        required=False,
        widget=forms.Select(attrs={"class": "input"}),
        empty_label="— Sin usuario vinculado —",
    )

    services = forms.ModelMultipleChoiceField(
        label="Servicios que realiza",
        queryset=Service.objects.filter(is_active=True).order_by("name"),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "input", "size": "8"}),
    )

    class Meta:
        model = Employee
        fields = [
            "user",
            "first_name",
            "last_name",
            "phone",
            "email",
            "services",
            "calendar_color",
            "commission_percent",
            "is_active",
            "notes",
        ]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "input", "placeholder": "Nombre"}),
            "last_name": forms.TextInput(attrs={"class": "input", "placeholder": "Apellidos"}),
            "phone": forms.TextInput(attrs={"class": "input", "placeholder": "Teléfono"}),
            "email": forms.EmailInput(attrs={"class": "input", "placeholder": "Email"}),
            "calendar_color": forms.TextInput(attrs={"class": "input", "type": "color"}),
            "commission_percent": forms.NumberInput(attrs={"class": "input", "min": "0", "max": "100", "step": "0.01"}),
            "notes": forms.Textarea(attrs={"class": "textarea", "placeholder": "Notas internas", "rows": 5}),
            "is_active": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }


class BaseScheduleFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()

        for form in self.forms:
            if not hasattr(form, "cleaned_data") or form.cleaned_data.get("DELETE"):
                continue

            has_payload = any(
                form.cleaned_data.get(field)
                for field in ("date", "start_time", "end_time", "break_start", "break_end", "label", "note")
            ) or bool(form.cleaned_data.get("is_day_off"))
            if not has_payload:
                continue

            is_day_off = form.cleaned_data.get("is_day_off")
            start_time = form.cleaned_data.get("start_time")
            end_time = form.cleaned_data.get("end_time")
            break_start = form.cleaned_data.get("break_start")
            break_end = form.cleaned_data.get("break_end")

            if is_day_off:
                continue

            if not start_time or not end_time:
                raise forms.ValidationError("Cada turno laborable debe tener hora de inicio y fin.")

            if end_time <= start_time:
                raise forms.ValidationError("La hora de fin debe ser posterior al inicio.")

            if bool(break_start) != bool(break_end):
                raise forms.ValidationError("La pausa debe tener hora de inicio y fin.")

            if break_start and break_end:
                if break_end <= break_start:
                    raise forms.ValidationError("La pausa debe terminar después de empezar.")
                if break_start <= start_time or break_end >= end_time:
                    raise forms.ValidationError("La pausa debe quedar dentro del turno.")


class WeeklyShiftForm(forms.ModelForm):
    class Meta:
        model = EmployeeWeeklyShift
        fields = ["weekday", "is_day_off", "start_time", "end_time", "break_start", "break_end", "note"]
        widgets = {
            "weekday": forms.HiddenInput(),
            "is_day_off": forms.CheckboxInput(attrs={"class": "checkbox js-day-off"}),
            "start_time": forms.TimeInput(attrs={"class": "input", "type": "time"}),
            "end_time": forms.TimeInput(attrs={"class": "input", "type": "time"}),
            "break_start": forms.TimeInput(attrs={"class": "input", "type": "time"}),
            "break_end": forms.TimeInput(attrs={"class": "input", "type": "time"}),
            "note": forms.TextInput(attrs={"class": "input", "placeholder": "Ej. turno partido"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["weekday"].initial = self.instance.weekday if self.instance.pk else self.initial.get("weekday")
        self.weekday_label = Weekday(self.fields["weekday"].initial).label


class ScheduleOverrideForm(forms.ModelForm):
    class Meta:
        model = EmployeeScheduleOverride
        fields = ["date", "is_day_off", "start_time", "end_time", "break_start", "break_end", "label"]
        widgets = {
            "date": forms.DateInput(attrs={"class": "input", "type": "date"}),
            "is_day_off": forms.CheckboxInput(attrs={"class": "checkbox js-day-off"}),
            "start_time": forms.TimeInput(attrs={"class": "input", "type": "time"}),
            "end_time": forms.TimeInput(attrs={"class": "input", "type": "time"}),
            "break_start": forms.TimeInput(attrs={"class": "input", "type": "time"}),
            "break_end": forms.TimeInput(attrs={"class": "input", "type": "time"}),
            "label": forms.TextInput(attrs={"class": "input", "placeholder": "Vacaciones, festivo, horario especial"}),
        }


WeeklyShiftFormSet = inlineformset_factory(
    Employee,
    EmployeeWeeklyShift,
    form=WeeklyShiftForm,
    formset=BaseScheduleFormSet,
    extra=0,
    can_delete=False,
)

ScheduleOverrideFormSet = inlineformset_factory(
    Employee,
    EmployeeScheduleOverride,
    form=ScheduleOverrideForm,
    formset=BaseScheduleFormSet,
    extra=3,
    can_delete=True,
)
