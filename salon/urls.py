from django.urls import path
from .views import zone_list, zone_create, zone_update, zone_delete

app_name = "salon"

urlpatterns = [
    path("", zone_list, name="list"),
    path("new/", zone_create, name="create"),
    path("<int:pk>/edit/", zone_update, name="update"),
    path("<int:pk>/delete/", zone_delete, name="delete"),
]