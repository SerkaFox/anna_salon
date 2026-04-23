from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, UserCreationForm

from employees.models import Employee

User = get_user_model()


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        label="Usuario",
        widget=forms.TextInput(attrs={
            "class": "input",
            "placeholder": "Usuario",
            "autofocus": True,
        }),
    )
    password = forms.CharField(
        label="Contraseña",
        widget=forms.PasswordInput(attrs={
            "class": "input",
            "placeholder": "Contraseña",
        }),
    )


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "phone"]
        widgets = {
            "username": forms.TextInput(attrs={"class": "input", "placeholder": "Usuario"}),
            "first_name": forms.TextInput(attrs={"class": "input", "placeholder": "Nombre"}),
            "last_name": forms.TextInput(attrs={"class": "input", "placeholder": "Apellidos"}),
            "email": forms.EmailInput(attrs={"class": "input", "placeholder": "Email"}),
            "phone": forms.TextInput(attrs={"class": "input", "placeholder": "Teléfono"}),
        }


class StyledPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({"class": "input"})


class UserAdminForm(UserCreationForm):
    employee = forms.ModelChoiceField(
        label="Empleado vinculado",
        queryset=Employee.objects.select_related("user").order_by("first_name", "last_name"),
        required=False,
        empty_label="— Sin vincular —",
        widget=forms.Select(attrs={"class": "input"}),
    )
    role = forms.ChoiceField(
        label="Rol",
        choices=User.ROLE_CHOICES,
        widget=forms.Select(attrs={"class": "input"}),
    )
    first_name = forms.CharField(label="Nombre", required=False, widget=forms.TextInput(attrs={"class": "input"}))
    last_name = forms.CharField(label="Apellidos", required=False, widget=forms.TextInput(attrs={"class": "input"}))
    email = forms.EmailField(label="Email", required=False, widget=forms.EmailInput(attrs={"class": "input"}))
    phone = forms.CharField(label="Teléfono", required=False, widget=forms.TextInput(attrs={"class": "input"}))
    is_active = forms.BooleanField(label="Cuenta activa", required=False, widget=forms.CheckboxInput(attrs={"class": "checkbox"}))

    password1 = forms.CharField(label="Contraseña", widget=forms.PasswordInput(attrs={"class": "input"}))
    password2 = forms.CharField(label="Confirmar contraseña", widget=forms.PasswordInput(attrs={"class": "input"}))

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ["username", "first_name", "last_name", "email", "phone", "role", "is_active", "employee"]
        widgets = {
            "username": forms.TextInput(attrs={"class": "input", "placeholder": "Usuario"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update({"class": "input", "placeholder": "Usuario"})
        if self.instance.pk:
            linked_employee = Employee.objects.filter(user=self.instance).first()
            self.fields["employee"].initial = linked_employee

    def clean_employee(self):
        employee = self.cleaned_data.get("employee")
        if employee and employee.user and employee.user != self.instance:
            raise forms.ValidationError("Este empleado ya está vinculado a otra cuenta.")
        return employee

    def save(self, commit=True):
        employee = self.cleaned_data.get("employee")
        user = super().save(commit=False)
        user.role = self.cleaned_data["role"]
        user.email = self.cleaned_data.get("email", "")
        user.phone = self.cleaned_data.get("phone", "")
        user.first_name = self.cleaned_data.get("first_name", "")
        user.last_name = self.cleaned_data.get("last_name", "")
        user.is_active = self.cleaned_data.get("is_active", True)

        if commit:
            user.save()
            Employee.objects.filter(user=user).exclude(pk=getattr(employee, "pk", None)).update(user=None)
            if employee:
                employee.user = user
                employee.save(update_fields=["user"])
        return user


class UserAdminUpdateForm(forms.ModelForm):
    employee = forms.ModelChoiceField(
        label="Empleado vinculado",
        queryset=Employee.objects.select_related("user").order_by("first_name", "last_name"),
        required=False,
        empty_label="— Sin vincular —",
        widget=forms.Select(attrs={"class": "input"}),
    )

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "phone", "role", "is_active"]
        widgets = {
            "username": forms.TextInput(attrs={"class": "input", "placeholder": "Usuario"}),
            "first_name": forms.TextInput(attrs={"class": "input", "placeholder": "Nombre"}),
            "last_name": forms.TextInput(attrs={"class": "input", "placeholder": "Apellidos"}),
            "email": forms.EmailInput(attrs={"class": "input", "placeholder": "Email"}),
            "phone": forms.TextInput(attrs={"class": "input", "placeholder": "Teléfono"}),
            "role": forms.Select(attrs={"class": "input"}),
            "is_active": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        linked_employee = Employee.objects.filter(user=self.instance).first()
        self.fields["employee"].initial = linked_employee

    def clean_employee(self):
        employee = self.cleaned_data.get("employee")
        if employee and employee.user and employee.user != self.instance:
            raise forms.ValidationError("Este empleado ya está vinculado a otra cuenta.")
        return employee

    def save(self, commit=True):
        employee = self.cleaned_data.get("employee")
        user = super().save(commit=commit)
        if commit:
            Employee.objects.filter(user=user).exclude(pk=getattr(employee, "pk", None)).update(user=None)
            if employee:
                employee.user = user
                employee.save(update_fields=["user"])
        return user
