from django import forms

from .models import Payment


class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ["paid_at", "amount", "method", "reference", "notes"]
        widgets = {
            "paid_at": forms.DateTimeInput(
                attrs={"class": "input", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "amount": forms.NumberInput(attrs={"class": "input", "step": "0.01", "min": "0.01"}),
            "method": forms.Select(attrs={"class": "input"}),
            "reference": forms.TextInput(attrs={"class": "input", "placeholder": "Ticket, id Bizum, transferencia"}),
            "notes": forms.Textarea(attrs={"class": "textarea", "rows": 3, "placeholder": "Notas internas de cobro"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["paid_at"].input_formats = ("%Y-%m-%dT%H:%M",)
