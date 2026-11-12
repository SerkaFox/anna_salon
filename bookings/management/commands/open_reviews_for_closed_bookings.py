"""Deja aptas para reseña reservas ya cerradas a las que les falta `completed_at`.

Las reservas que se cerraron en bloque al dejar Treatwell se guardaron sin
`completed_at`, y sin ese campo `queue_due_review_requests` ni las mira. Esta
orden lo rellena con el fin real de la cita para un rango concreto, de forma
que el atrasado entre en el reparto normal: tope diario y horario diurno los
pone el cron, aqui no se envia nada.

Es de un solo uso: una vez repartido el atrasado no hace falta volver a
ejecutarla, porque close_past_bookings ya deja `completed_at` puesto.
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from bookings.models import Booking


class Command(BaseCommand):
    help = "Rellena completed_at en reservas cerradas para que puedan pedir reseña."

    def add_arguments(self, parser):
        parser.add_argument(
            "--since",
            required=True,
            help="Solo reservas que terminen en esta fecha o despues (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Aplica los cambios. Sin esta opcion solo informa.",
        )

    def handle(self, *args, **options):
        bookings = Booking.objects.filter(
            status=Booking.Statuses.DONE,
            completed_at__isnull=True,
            end_at__date__gte=options["since"],
        )
        total = bookings.count()
        if not total:
            self.stdout.write("No hay reservas cerradas pendientes en ese rango.")
            return

        if not options["apply"]:
            self.stdout.write(
                f"{total} reservas quedarian aptas para reseña. "
                "Repite con --apply para aplicarlo."
            )
            return

        now = timezone.now()
        updated = 0
        for booking in bookings.only("id", "end_at"):
            Booking.objects.filter(pk=booking.pk).update(
                completed_at=booking.end_at, updated_at=now
            )
            updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"{updated} reservas aptas para reseña. El cron las ira "
                "repartiendo con su tope diario."
            )
        )
