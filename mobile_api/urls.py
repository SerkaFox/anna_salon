from django.urls import path

from . import views


app_name = "mobile_api"

urlpatterns = [
    path("me/", views.MeView.as_view(), name="me"),
    path("clients/", views.ClientListView.as_view(), name="clients"),
    path("clients/<int:pk>/", views.ClientDetailView.as_view(), name="client_detail"),
    path("employees/", views.EmployeeListView.as_view(), name="employees"),
    path("employees/<int:pk>/", views.EmployeeDetailView.as_view(), name="employee_detail"),
    path("services/", views.ServiceListView.as_view(), name="services"),
    path("zones/", views.ZoneListView.as_view(), name="zones"),
    path("bookings/", views.BookingListCreateView.as_view(), name="bookings"),
    path("bookings/check-availability/", views.BookingAvailabilityCheckView.as_view(), name="booking_check_availability"),
    path("availability/slots/", views.AvailabilitySlotsView.as_view(), name="availability_slots"),
    path("bookings/<int:pk>/", views.BookingDetailView.as_view(), name="booking_detail"),
    path("bookings/<int:pk>/reschedule/", views.BookingRescheduleView.as_view(), name="booking_reschedule"),
    path("bookings/<int:pk>/status/", views.BookingStatusView.as_view(), name="booking_status"),
    path("time-blocks/", views.TimeBlockListCreateView.as_view(), name="time_blocks"),
    path("time-blocks/<str:pk>/", views.TimeBlockDetailView.as_view(), name="time_block_detail"),
    path("calendar/day/", views.CalendarDayView.as_view(), name="calendar_day"),
]
