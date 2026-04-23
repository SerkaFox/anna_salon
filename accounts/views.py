from django.contrib import messages
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy

from accounts.permissions import admin_required
from auditlog.services import log_event
from employees.models import Employee

from .forms import (
    LoginForm,
    StyledPasswordChangeForm,
    UserAdminForm,
    UserAdminUpdateForm,
    UserProfileForm,
)

User = get_user_model()


class UserLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def get_success_url(self):
        return reverse_lazy("dashboard:home")


def user_logout(request):
    logout(request)
    return redirect("accounts:login")


@login_required
def profile_view(request):
    if request.method == "POST":
        form = UserProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            log_event(
                actor=request.user,
                section="account",
                action="profile_update",
                instance=request.user,
                message=f"Perfil actualizado por {request.user.username}.",
            )
            messages.success(request, "Perfil actualizado.")
            return redirect("accounts:profile")
    else:
        form = UserProfileForm(instance=request.user)

    return render(
        request,
        "accounts/profile.html",
        {
            "active_section": "profile",
            "form": form,
            "linked_employee": Employee.objects.filter(user=request.user).first(),
        },
    )


@login_required
def change_password_view(request):
    if request.method == "POST":
        form = StyledPasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            log_event(
                actor=request.user,
                section="account",
                action="password_change",
                instance=user,
                message=f"Contraseña cambiada para {user.username}.",
            )
            messages.success(request, "Contraseña actualizada.")
            return redirect("accounts:profile")
    else:
        form = StyledPasswordChangeForm(request.user)

    return render(
        request,
        "accounts/change_password.html",
        {
            "active_section": "profile",
            "form": form,
        },
    )


@login_required
@admin_required
def user_list(request):
    query = (request.GET.get("q") or "").strip()
    users = User.objects.all().order_by("username")
    if query:
        users = users.filter(username__icontains=query) | users.filter(first_name__icontains=query) | users.filter(last_name__icontains=query)
    users = users.distinct()
    user_rows = []
    for user in users:
        user_rows.append(
            {
                "user": user,
                "employee": Employee.objects.filter(user=user).first(),
            }
        )
    return render(
        request,
        "accounts/user_list.html",
        {
            "active_section": "accounts",
            "user_rows": user_rows,
            "query": query,
            "users_count": len(user_rows),
        },
    )


@login_required
@admin_required
def user_create(request):
    if request.method == "POST":
        form = UserAdminForm(request.POST)
        if form.is_valid():
            user = form.save()
            log_event(
                actor=request.user,
                section="account",
                action="create",
                instance=user,
                message=f"Cuenta creada: {user.username}.",
                metadata={"role": user.role},
            )
            messages.success(request, f"Cuenta creada: {user.username}")
            return redirect("accounts:user_list")
    else:
        form = UserAdminForm(initial={"is_active": True, "role": User.ROLE_EMPLOYEE})

    return render(
        request,
        "accounts/user_form.html",
        {
            "active_section": "accounts",
            "form": form,
            "is_edit": False,
            "managed_user": None,
        },
    )


@login_required
@admin_required
def user_update(request, pk):
    managed_user = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        form = UserAdminUpdateForm(request.POST, instance=managed_user)
        if form.is_valid():
            form.save()
            log_event(
                actor=request.user,
                section="account",
                action="update",
                instance=managed_user,
                message=f"Cuenta actualizada: {managed_user.username}.",
                metadata={"role": managed_user.role},
            )
            messages.success(request, f"Cuenta actualizada: {managed_user.username}")
            return redirect("accounts:user_list")
    else:
        form = UserAdminUpdateForm(instance=managed_user)

    return render(
        request,
        "accounts/user_form.html",
        {
            "active_section": "accounts",
            "form": form,
            "is_edit": True,
            "managed_user": managed_user,
        },
    )
