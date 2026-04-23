#clients/urls.py
from django.urls import path
from .views import (
    client_list,
    client_create,
    client_create_api,
    client_update,
    client_delete,
    client_detail,
    use_referral_reward,
)

app_name = "clients"

urlpatterns = [
    path("", client_list, name="list"),
    path("new/", client_create, name="create"),
    path("api/new/", client_create_api, name="create_api"),
    path("<int:pk>/", client_detail, name="detail"),
    path("<int:pk>/edit/", client_update, name="update"),
    path("<int:pk>/delete/", client_delete, name="delete"),
    path("<int:pk>/use-reward/", use_referral_reward, name="use_reward"),
]
