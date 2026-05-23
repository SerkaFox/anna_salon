from django.contrib import admin
from django.urls import include, path

from core.views import home


urlpatterns = [
    path("", home, name="home"),
    path("", include("accounts.urls")),
    path("api/v1/", include("mobile_api.urls")),
    path("panel/", include("dashboard.urls")),
    path("panel/clientes/", include("clients.urls")),
    path("panel/empleados/", include("employees.urls")),
    path("panel/servicios/", include("services_app.urls")),
    path("panel/zonas/", include("salon.urls")),
    path("panel/reservas/", include("bookings.urls")),
    path("panel/documentos/", include("documents.urls")),
    path("panel/auditoria/", include("auditlog.urls")),
    path("dj-admin/", admin.site.urls),
]
