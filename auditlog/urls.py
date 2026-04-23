from django.urls import path

from .views import event_list


app_name = "auditlog"

urlpatterns = [
    path("", event_list, name="list"),
]
