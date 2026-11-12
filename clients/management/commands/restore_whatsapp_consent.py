"""Repara el flag notify_whatsapp de los clientes importados de Treatwell.

La importacion de julio de 2026 leia `shouldreceivemarketingsmsnotifications`,
un campo que no existe en la exportacion de Treatwell: `_bool(None)` devolvia
False y los 1228 clientes importados quedaron marcados como si hubieran
rechazado las notificaciones. No fue una decision suya, y de hecho a esos
mismos clientes se les siguieron enviando recordatorios, recibos y peticiones
de prepago, porque el flag solo se mira al pedir reseña.

Para no escribir a contactos dormidos, que es de donde salen las quejas y los
bloqueos de la linea, solo se reactivan los clientes que han pasado por el
salon hace poco (--days, 90 por defecto).
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from bookings.models import Booking
from clients.models import Client


class Command(BaseCommand):
    help = "Reactiva notify_whatsapp en clientes importados con cita reciente."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=90,
            help="Ventana de actividad: solo clientes con cita en estos ultimos dias.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Aplica los cambios. Sin esta opcion solo informa.",
        )

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=options["days"])
        recent_client_ids = set(
            Booking.objects.filter(end_at__gte=cutoff)
            .exclude(status=Booking.Statuses.CANCELLED)
            .values_list("client_id", flat=True)
        )
        candidates = (
            Client.objects.filter(notify_whatsapp=False, is_active=True)
            .exclude(phone="")
            .exclude(is_blacklisted=True)
        )
        ids = [c.pk for c in candidates if c.pk in recent_client_ids]

        if not ids:
            self.stdout.write("No hay clientes que reactivar.")
            return

        if not options["apply"]:
            self.stdout.write(
                f"{len(ids)} clientes con cita en los ultimos {options['days']} dias "
                "volverian a recibir notificaciones. Repite con --apply."
            )
            return

        updated = Client.objects.filter(pk__in=ids).update(
            notify_whatsapp=True, updated_at=timezone.now()
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Reactivados {updated} clientes (actividad en {options['days']} dias)."
            )
        )
