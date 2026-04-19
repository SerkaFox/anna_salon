from django.urls import path

from .views import (
    service_create,
    service_delete,
    service_list,
    service_update,
)

app_name = "services_app"

urlpatterns = [
    path("", service_list, name="list"),
    path("new/", service_create, name="create"),
    path("<int:pk>/edit/", service_update, name="update"),
    path("<int:pk>/delete/", service_delete, name="delete"),
]