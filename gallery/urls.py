from django.urls import path

from . import views


app_name = "gallery"

urlpatterns = [
    path("", views.instagram_post_list, name="list"),
    path("new/", views.instagram_post_create, name="create"),
    path("<int:pk>/edit/", views.instagram_post_update, name="update"),
    path("<int:pk>/delete/", views.instagram_post_delete, name="delete"),
]
