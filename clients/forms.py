from django import forms

from .models import Client


class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = [
            "first_name",
            "last_name",
            "phone",
            "email",
            "birth_date",
            "referred_by",
            "notes",
            "is_active",
        ]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "input"}),
            "last_name": forms.TextInput(attrs={"class": "input"}),
            "phone": forms.TextInput(attrs={"class": "input"}),
            "email": forms.EmailInput(attrs={"class": "input"}),
            "birth_date": forms.DateInput(attrs={"class": "input", "type": "date"}),
            "referred_by": forms.Select(attrs={"class": "input"}),
            "notes": forms.Textarea(attrs={"class": "textarea", "rows": 5}),
            "is_active": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["referred_by"].required = False
        self.fields["referred_by"].queryset = Client.objects.filter(is_active=True).order_by("first_name", "last_name")
        self.fields["referred_by"].label_from_instance = lambda obj: obj.full_name or str(obj)

        if self.instance.pk:
            self.fields["referred_by"].queryset = self.fields["referred_by"].queryset.exclude(pk=self.instance.pk)