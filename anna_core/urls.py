from django.contrib import admin
from django.shortcuts import redirect
from django.urls import include, path


def root_redirect(request):
    if request.user.is_authenticated:
        return redirect("dashboard:home")
    return redirect("accounts:login")


urlpatterns = [
    path("", root_redirect),
    path("", include("accounts.urls")),
    path("panel/", include("dashboard.urls")),
    path("panel/clientes/", include("clients.urls")),
    path("panel/empleados/", include("employees.urls")),
    path("panel/servicios/", include("services_app.urls")),
    path("panel/zonas/", include("salon.urls")),
    path("panel/reservas/", include("bookings.urls")),
    path("dj-admin/", admin.site.urls),
]