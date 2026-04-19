from django.urls import path
from .views import employee_list, employee_create, employee_update, employee_delete

app_name = "employees"

urlpatterns = [
    path("", employee_list, name="list"),
    path("new/", employee_create, name="create"),
    path("<int:pk>/edit/", employee_update, name="update"),
    path("<int:pk>/delete/", employee_delete, name="delete"),
]