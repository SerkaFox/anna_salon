from django.urls import path

from . import views


app_name = "documents"

urlpatterns = [
    path("", views.document_list, name="list"),
    path("pendientes/", views.unpaid_documents, name="unpaid"),
    path("caja/", views.cashbox, name="cashbox"),
    path("caja/cerrar/", views.cashbox_close, name="cashbox_close"),
    path("caja/export/csv/", views.cashbox_export_csv, name="cashbox_export_csv"),
    path("caja/print/", views.cashbox_print, name="cashbox_print"),
    path("export/csv/", views.document_export_csv, name="export_csv"),
    path("booking/<int:booking_pk>/<str:document_type>/create/", views.document_create_from_booking, name="create_from_booking"),
    path("booking/<int:booking_pk>/pay/quick/", views.booking_quick_payment, name="booking_quick_payment"),
    path("<int:pk>/", views.document_detail, name="detail"),
    path("<int:pk>/print/", views.document_print, name="print"),
    path("<int:pk>/refund/quick/", views.document_quick_refund, name="quick_refund"),
    path("<int:document_pk>/payments/create/", views.payment_create, name="payment_create"),
    path("payments/<int:pk>/edit/", views.payment_edit, name="payment_edit"),
    path("payments/<int:pk>/delete/", views.payment_delete, name="payment_delete"),
]
