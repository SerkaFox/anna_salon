from django.urls import path

from . import views


app_name = "payments"

urlpatterns = [
    path("pagar/<str:reference>/", views.public_payment, name="public_payment"),
    path("redsys/notification/", views.redsys_notification, name="redsys_notification"),
    path("redsys/success/", views.redsys_success, name="redsys_success"),
    path("redsys/error/", views.redsys_error, name="redsys_error"),
    path("stripe/success/", views.stripe_success, name="stripe_success"),
    path("stripe/cancel/", views.stripe_cancel, name="stripe_cancel"),
    path("stripe/webhook/", views.stripe_webhook, name="stripe_webhook"),
    path("demo/pay/<int:pk>/", views.demo_pay, name="demo_pay"),
]
