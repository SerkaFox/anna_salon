"""Cierra las reservas ya pasadas que nadie ha marcado como hechas.

Hasta octubre de 2026 el estado "Hecha" lo ponia la sincronizacion de Treatwell
(`checked_out` -> `done`, ver bookings/treatwell.py). Al dejar de usar Treatwell
las reservas se quedaron en "Confirmada" para siempre, y como las estadisticas
del empleado solo suman reservas en estado `done`, la ficha de cada empleada
pasó a mostrar 0,00 EUR.

Al cerrar se rellena `completed_at`, que es lo que deja la reserva lista para
que `queue_due_review_requests` pida la reseña. Ese reparto tiene tope diario y
horario diurno propios, asi que cerrar de madrugada no dispara ninguna rafaga.
Con --no-reviews se cierra dejando `completed_at` vacio y esa reserva no
recibira peticion nunca.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from bookings.models import Booking


CLOSEABLE_STATUSES = (Booking.Statuses.CONFIRMED, Booking.Statuses.IN_PROGRESS)


class Command(BaseCommand):
    help = "Marca como hechas las reservas pasadas que siguen confirmadas o en curso."

    def add_arguments(self, parser):
        parser.add_argument(
            "--grace-hours",
            type=int,
            default=3,
            help=(
                "Horas de margen tras el fin de la reserva antes de cerrarla, "
                "para que el personal pueda corregirla o marcarla como no asistida."
            ),
        )
        parser.add_argument(
            "--since",
            default="",
            help="Solo reservas que empiecen en esta fecha o despues (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--no-reviews",
            action="store_true",
            help=(
                "Cierra sin rellenar completed_at: esas reservas no recibiran "
                "nunca peticion de reseña."
            ),
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Aplica los cambios. Sin esta opcion solo informa.",
        )

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(hours=options["grace_hours"])
        bookings = Booking.objects.filter(
            end_at__lt=cutoff, status__in=CLOSEABLE_STATUSES
        )
        if options["since"]:
            bookings = bookings.filter(start_at__date__gte=options["since"])

        ids = list(bookings.values_list("id", flat=True))
        if not ids:
            self.stdout.write("No hay reservas pasadas pendientes de cerrar.")
            return

        if not options["apply"]:
            self.stdout.write(
                f"{len(ids)} reservas se cerrarian "
                f"(aptas para reseña: {'no' if options['no_reviews'] else 'si'}). "
                "Repite con --apply para aplicarlo."
            )
            return

        now = timezone.now()
        closed = 0
        if options["no_reviews"]:
            closed = Booking.objects.filter(id__in=ids).update(
                status=Booking.Statuses.DONE, updated_at=now
            )
        else:
            # completed_at = fin real de la cita, no "ahora": asi la antiguedad
            # de la visita se mide bien al decidir si todavia toca pedir reseña.
            for booking in Booking.objects.filter(id__in=ids).only("id", "end_at"):
                Booking.objects.filter(pk=booking.pk).update(
                    status=Booking.Statuses.DONE,
                    completed_at=booking.end_at,
                    updated_at=now,
                )
                closed += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Cerradas {closed} reservas pasadas "
                f"(aptas para reseña: {'no' if options['no_reviews'] else 'si'})."
            )
        )
