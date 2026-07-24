from django import forms

from .models import ClientReview


class ClientReviewForm(forms.ModelForm):
    class Meta:
        model = ClientReview
        fields = ("rating", "text")
        widgets = {
            "rating": forms.RadioSelect(
                choices=[(value, f"{value} / 5") for value in range(5, 0, -1)]
            ),
            "text": forms.Textarea(
                attrs={
                    "rows": 5,
                    "maxlength": 2000,
                    "placeholder": "Cuentanos como fue tu experiencia.",
                }
            ),
        }
