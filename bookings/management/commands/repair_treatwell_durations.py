from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from bookings.models import Booking


class Command(BaseCommand):
    help = "Convierte a minutos las duraciones Treatwell importadas originalmente como segundos."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        bookings = list(
            Booking.objects.filter(
                external_source="treatwell",
                duration_snapshot__gt=600,
            ).only("id", "start_at", "end_at", "duration_snapshot")
        )
        invalid = [booking.id for booking in bookings if booking.duration_snapshot % 60]
        if invalid:
            raise CommandError(
                f"Hay {len(invalid)} duraciones no divisibles por 60; no se modifico nada."
            )
        if not options["apply"]:
            self.stdout.write(f"Se corregirian {len(bookings)} reservas Treatwell.")
            return

        for booking in bookings:
            minutes = booking.duration_snapshot // 60
            booking.duration_snapshot = minutes
            booking.end_at = booking.start_at + timedelta(minutes=minutes)
        with transaction.atomic():
            Booking.objects.bulk_update(
                bookings,
                ["duration_snapshot", "end_at"],
                batch_size=500,
            )
        self.stdout.write(self.style.SUCCESS(f"Corregidas {len(bookings)} reservas Treatwell."))
