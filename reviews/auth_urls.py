from django.urls import path
from . import auth_views

app_name = "reviews_auth"

urlpatterns = [
    path("", auth_views.google_auth_status, name="google_status"),
    path("start/", auth_views.google_auth_start, name="google_start"),
    path("callback/", auth_views.google_auth_callback, name="google_callback"),
]
