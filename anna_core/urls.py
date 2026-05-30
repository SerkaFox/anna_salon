from django.contrib import admin
from django.urls import include, path

from core import views as core_views


urlpatterns = [
    path("", core_views.home, name="home"),
    path("servicios/", core_views.service_index, name="service_index"),
    path("servicios/<slug:slug>/", core_views.service_detail, name="service_detail"),
    path("consejos/", core_views.advice_index, name="advice_index"),
    path("consejos/<slug:slug>/", core_views.article_detail, name="article_detail"),
    path("reservar/", core_views.public_booking, name="public_booking"),
    path("reservar/slots/", core_views.public_booking_slots, name="public_booking_slots"),
    path("reservar/lista-espera/", core_views.public_waitlist, name="public_waitlist"),
    path("set-language/", core_views.set_public_language, name="set_public_language"),
    path("robots.txt", core_views.robots_txt, name="robots_txt"),
    path("sitemap.xml", core_views.sitemap_xml, name="sitemap_xml"),
    path("", include("accounts.urls")),
    path("api/v1/", include("mobile_api.urls")),
    path("payments/", include("payments.urls")),
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
