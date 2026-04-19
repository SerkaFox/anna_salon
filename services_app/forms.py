from django import forms

from .models import Service

class ServiceForm(forms.ModelForm):
    class Meta:
        model = Service
        fields = [
            "name",
            "description",
            "duration_minutes",
            "price",
            "requires_zone",
            "allowed_zones",
            "is_active",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input", "placeholder": "Nombre del servicio"}),
            "description": forms.Textarea(attrs={"class": "textarea", "placeholder": "Descripción", "rows": 5}),
            "duration_minutes": forms.NumberInput(attrs={"class": "input", "placeholder": "Duración en minutos", "min": "1"}),
            "price": forms.NumberInput(attrs={"class": "input", "placeholder": "Precio", "step": "0.01", "min": "0"}),
            "requires_zone": forms.CheckboxInput(attrs={"class": "checkbox"}),
            "allowed_zones": forms.SelectMultiple(attrs={"class": "input", "size": "6"}),
            "is_active": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }

    def clean(self):
        cleaned_data = super().clean()
        requires_zone = cleaned_data.get("requires_zone")
        allowed_zones = cleaned_data.get("allowed_zones")

        if requires_zone and not allowed_zones:
            self.add_error("allowed_zones", "Selecciona al menos una zona para este servicio.")

        if not requires_zone:
            cleaned_data["allowed_zones"] = []

        return cleaned_data