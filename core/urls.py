from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("servicios/", views.service_index, name="service_index"),
    path("servicios/<slug:slug>/", views.service_detail, name="service_detail"),
    path("consejos/", views.advice_index, name="advice_index"),
    path("consejos/<slug:slug>/", views.article_detail, name="article_detail"),
    path("reservar/", views.public_booking, name="public_booking"),
    path("reservar/slots/", views.public_booking_slots, name="public_booking_slots"),
    path("set-language/", views.set_public_language, name="set_public_language"),
    path("robots.txt", views.robots_txt, name="robots_txt"),
    path("sitemap.xml", views.sitemap_xml, name="sitemap_xml"),
]
