from django.urls import path
from .views import (
    booking_list,
    booking_create,
    booking_update,
    booking_delete,
    service_data_api,
    client_reward_api,
    booking_slot_check_api,
    booking_availability,
    booking_calendar_day,
    booking_reschedule_api,
    booking_status_api,
)

app_name = "bookings"

urlpatterns = [
    path("", booking_list, name="list"),
    path("new/", booking_create, name="create"),
    path("calendar/", booking_calendar_day, name="calendar_day"),
    path("availability/", booking_availability, name="availability"),
    path("api/service-data/", service_data_api, name="service_data_api"),
    path("api/client-reward/", client_reward_api, name="client_reward_api"),
    path("api/<int:pk>/reschedule/", booking_reschedule_api, name="reschedule_api"),
    path("api/<int:pk>/status/", booking_status_api, name="status_api"),
    path("<int:pk>/edit/", booking_update, name="update"),
    path("api/slot-check/", booking_slot_check_api, name="slot_check_api"),
    path("<int:pk>/delete/", booking_delete, name="delete"),
]
