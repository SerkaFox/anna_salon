from django.urls import path

from . import views

app_name = "whatsapp_bot"

urlpatterns = [
    path("connect/<str:token>/", views.login_link, name="login_link"),
]
