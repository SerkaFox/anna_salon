from django.urls import path
from .views import (
    UserLoginView,
    change_password_view,
    profile_view,
    user_create,
    user_list,
    user_logout,
    user_update,
)

app_name = "accounts"

urlpatterns = [
    path("login/", UserLoginView.as_view(), name="login"),
    path("logout/", user_logout, name="logout"),
    path("mi-perfil/", profile_view, name="profile"),
    path("mi-perfil/password/", change_password_view, name="change_password"),
    path("panel/cuentas/", user_list, name="user_list"),
    path("panel/cuentas/nueva/", user_create, name="user_create"),
    path("panel/cuentas/<int:pk>/editar/", user_update, name="user_update"),
]
