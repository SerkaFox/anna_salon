from django.urls import path

from . import views

app_name = "whatsapp_bot"

urlpatterns = [
    path("connect/<str:name>/", views.whatsapp_connect, name="connect"),
    path("connect/<str:name>/qr.png", views.whatsapp_qr_image, name="qr_image"),
    path("token/<str:token>/", views.login_link, name="login_link"),
    path("button-reply/", views.button_reply_webhook, name="button_reply"),
]
