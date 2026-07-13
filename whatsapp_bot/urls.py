from django.urls import path

from . import views

app_name = "whatsapp_bot"

urlpatterns = [
    path("connect/<str:name>/", views.whatsapp_connect, name="connect"),
    path("token/<str:token>/", views.login_link, name="login_link"),
]
