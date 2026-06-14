from django import forms

from .models import InstagramPost


class InstagramPostForm(forms.ModelForm):
    class Meta:
        model = InstagramPost
        fields = [
            "title",
            "instagram_url",
            "embed_html",
            "caption",
            "active",
            "featured",
            "sort_order",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": "input"}),
            "instagram_url": forms.URLInput(attrs={"class": "input"}),
            "embed_html": forms.Textarea(attrs={"class": "textarea", "rows": 8}),
            "caption": forms.Textarea(attrs={"class": "textarea", "rows": 4}),
            "sort_order": forms.NumberInput(attrs={"class": "input", "min": 0}),
        }
