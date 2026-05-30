from django.urls import path

from . import views


app_name = "payments"

urlpatterns = [
    path("redsys/notification/", views.redsys_notification, name="redsys_notification"),
    path("redsys/success/", views.redsys_success, name="redsys_success"),
    path("redsys/error/", views.redsys_error, name="redsys_error"),
]
