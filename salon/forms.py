from django import forms
from .models import SalonSettings, Zone


class DepositSettingsForm(forms.ModelForm):
    class Meta:
        model = SalonSettings
        fields = ["deposit_percent", "deposit_minimum_amount", "deposit_rounding"]
        widgets = {
            "deposit_percent": forms.NumberInput(
                attrs={"class": "input", "min": "0", "max": "100", "step": "0.01"}
            ),
            "deposit_minimum_amount": forms.NumberInput(
                attrs={"class": "input", "min": "0", "step": "0.01"}
            ),
            "deposit_rounding": forms.Select(attrs={"class": "input"}),
        }


class ZoneForm(forms.ModelForm):
    class Meta:
        model = Zone
        fields = [
            "name",
            "zone_type",
            "capacity",
            "color",
            "is_active",
            "notes",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input", "placeholder": "Nombre de la zona"}),
            "zone_type": forms.Select(attrs={"class": "input"}),
            "capacity": forms.NumberInput(attrs={"class": "input", "min": "1"}),
            "color": forms.TextInput(attrs={"class": "input", "type": "color"}),
            "is_active": forms.CheckboxInput(attrs={"class": "checkbox"}),
            "notes": forms.Textarea(attrs={"class": "textarea", "placeholder": "Notas internas", "rows": 5}),
        }
