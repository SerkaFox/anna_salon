from django.urls import path

from . import views


app_name = "documents"

urlpatterns = [
    path("", views.document_list, name="list"),
    path("export/csv/", views.document_export_csv, name="export_csv"),
    path("booking/<int:booking_pk>/<str:document_type>/create/", views.document_create_from_booking, name="create_from_booking"),
    path("<int:pk>/", views.document_detail, name="detail"),
    path("<int:pk>/print/", views.document_print, name="print"),
]
