from django import forms
from django.contrib.auth import get_user_model

from .models import Client

User = get_user_model()


class ClientForm(forms.ModelForm):
    username = forms.CharField(
        label="Usuario app",
        required=False,
        widget=forms.TextInput(attrs={"class": "input", "placeholder": "Login del cliente"}),
    )
    password = forms.CharField(
        label="Nueva password app",
        required=False,
        min_length=4,
        widget=forms.PasswordInput(attrs={"class": "input", "placeholder": "Minimo 4 caracteres"}),
        help_text="Dejar vacio para mantener la password actual.",
    )

    def __init__(self, *args, **kwargs):
        allowed_referred_by = kwargs.pop("allowed_referred_by", None)
        self.can_manage_credentials = kwargs.pop("can_manage_credentials", False)
        super().__init__(*args, **kwargs)
        self.fields["referred_by"].required = False
        self.fields["referred_by"].queryset = Client.objects.filter(is_active=True).order_by("first_name", "last_name")
        self.fields["referred_by"].label_from_instance = lambda obj: obj.full_name or str(obj)

        if allowed_referred_by is not None:
            self.fields["referred_by"].queryset = allowed_referred_by

        if self.instance.pk:
            self.fields["referred_by"].queryset = self.fields["referred_by"].queryset.exclude(pk=self.instance.pk)
            if self.instance.user_id:
                self.fields["username"].initial = self.instance.user.username
                self.fields["username"].disabled = True
                self.fields["username"].help_text = "El usuario ya existe. Puedes copiarlo o cambiar solo la password."

        if not self.can_manage_credentials:
            self.fields.pop("username", None)
            self.fields.pop("password", None)

    def clean_username(self):
        username = (self.cleaned_data.get("username") or "").strip()
        if not username or "username" not in self.fields:
            return username
        exists = User.objects.filter(username=username)
        if self.instance.pk and self.instance.user_id:
            exists = exists.exclude(pk=self.instance.user_id)
        if exists.exists():
            raise forms.ValidationError("Este usuario ya existe.")
        return username

    def clean(self):
        cleaned = super().clean()
        if "username" not in self.fields:
            return cleaned
        username = (cleaned.get("username") or "").strip()
        password = cleaned.get("password") or ""
        if password and not username and not getattr(self.instance, "user_id", None):
            self.add_error("username", "Introduce un usuario para crear el acceso.")
        if username and not password and not getattr(self.instance, "user_id", None):
            self.add_error("password", "Introduce una password para crear el acceso.")
        return cleaned

    def save(self, commit=True):
        username = ""
        password = ""
        if "username" in self.fields:
            username = (self.cleaned_data.get("username") or "").strip()
            password = self.cleaned_data.get("password") or ""
        client = super().save(commit=commit)
        if commit and self.can_manage_credentials:
            self._sync_user(client, username, password)
        return client

    def _sync_user(self, client, username, password):
        if not username and not password:
            return
        user = client.user
        if user is None:
            user = User(username=username, role=User.ROLE_CLIENT)
        if username and not client.user_id:
            user.username = username
        user.first_name = client.first_name
        user.last_name = client.last_name
        user.email = client.email
        user.phone = client.phone
        user.role = User.ROLE_CLIENT
        user.is_active = client.is_active
        if password:
            user.set_password(password)
        user.save()
        if client.user_id != user.pk:
            client.user = user
            client.save(update_fields=["user"])

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
            "username",
            "password",
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

