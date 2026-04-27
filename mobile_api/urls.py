from django.urls import path

from . import views


app_name = "mobile_api"

urlpatterns = [
    path("me/", views.MeView.as_view(), name="me"),
    path("clients/", views.ClientListView.as_view(), name="clients"),
    path("employees/", views.EmployeeListView.as_view(), name="employees"),
    path("services/", views.ServiceListView.as_view(), name="services"),
    path("zones/", views.ZoneListView.as_view(), name="zones"),
    path("bookings/", views.BookingListCreateView.as_view(), name="bookings"),
    path("bookings/check-availability/", views.BookingAvailabilityCheckView.as_view(), name="booking_check_availability"),
    path("bookings/<int:pk>/", views.BookingDetailView.as_view(), name="booking_detail"),
    path("bookings/<int:pk>/reschedule/", views.BookingRescheduleView.as_view(), name="booking_reschedule"),
    path("bookings/<int:pk>/status/", views.BookingStatusView.as_view(), name="booking_status"),
    path("calendar/day/", views.CalendarDayView.as_view(), name="calendar_day"),
]
