#clients/urls.py
from django.urls import path
from .views import (
    client_list,
    client_create,
    client_create_api,
    client_update,
    client_delete,
    client_detail,
    client_portal,
    client_booking_detail,
    client_booking_payment,
    client_booking_cancel,
    client_booking_reschedule,
    client_booking_change_service,
    client_booking_document,
    client_booking_prepayment_refund,
    client_portal_slots_api,
    use_referral_reward,
    set_client_language,
)

app_name = "clients"

urlpatterns = [
    path("portal/", client_portal, name="portal"),
    path("portal/bookings/<int:pk>/", client_booking_detail, name="booking_detail"),
    path("portal/bookings/<int:pk>/payment/", client_booking_payment, name="booking_payment"),
    path("portal/bookings/<int:pk>/cancel/", client_booking_cancel, name="booking_cancel"),
    path("portal/bookings/<int:pk>/reschedule/", client_booking_reschedule, name="booking_reschedule"),
    path("portal/bookings/<int:pk>/service/", client_booking_change_service, name="booking_change_service"),
    path("portal/bookings/<int:pk>/document/", client_booking_document, name="booking_document"),
    path("portal/bookings/<int:pk>/prepayment/refund/", client_booking_prepayment_refund, name="booking_prepayment_refund"),
    path("portal/slots/", client_portal_slots_api, name="portal_slots_api"),
    path("portal/language/", set_client_language, name="set_language"),
    path("", client_list, name="list"),
    path("new/", client_create, name="create"),
    path("api/new/", client_create_api, name="create_api"),
    path("<int:pk>/", client_detail, name="detail"),
    path("<int:pk>/edit/", client_update, name="update"),
    path("<int:pk>/delete/", client_delete, name="delete"),
    path("<int:pk>/use-reward/", use_referral_reward, name="use_reward"),
]
