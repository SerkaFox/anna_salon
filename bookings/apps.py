from django.apps import AppConfig


class BookingsConfig(AppConfig):
    name = 'bookings'

    def ready(self):
        from . import signals  # noqa: F401
