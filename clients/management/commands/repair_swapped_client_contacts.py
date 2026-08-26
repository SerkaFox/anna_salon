import json
import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from clients.models import Client


def _phone_digits(value):
    return re.sub(r"\D", "", (value or "").strip())


def _looks_like_phone(value):
    text = (value or "").strip()
    digits = _phone_digits(text)
    return bool(re.fullmatch(r"[+\d\s()./-]+", text)) and 7 <= len(digits) <= 15


def _is_clear_swap(client):
    exported_name = (client.phone or "").strip()
    return (
        _looks_like_phone(client.first_name)
        and bool(exported_name)
        and not _looks_like_phone(exported_name)
        and not _phone_digits(exported_name)
    )


class Command(BaseCommand):
    help = "Repara clientes cuyo nombre y teléfono fueron intercambiados al importar."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Aplica la reparación. Sin esta opción solo muestra el recuento.",
        )
        parser.add_argument(
            "--backup",
            type=Path,
            help="Ruta JSON para guardar los valores originales antes de aplicar.",
        )

    def handle(self, *args, **options):
        clients = list(Client.objects.select_related("user").order_by("pk"))
        matches = [client for client in clients if _is_clear_swap(client)]
        self.stdout.write(f"Intercambios claros encontrados: {len(matches)}")
        if not options["apply"] or not matches:
            return

        timestamp = timezone.now().strftime("%Y%m%d-%H%M%S")
        backup_path = options.get("backup") or (
            Path(settings.BASE_DIR)
            / "backups"
            / f"client-contact-repair-{timestamp}.json"
        )
        backup_path = backup_path.resolve()
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup = [
            {
                "id": client.pk,
                "first_name": client.first_name,
                "last_name": client.last_name,
                "phone": client.phone,
            }
            for client in matches
        ]
        backup_path.write_text(
            json.dumps(backup, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        with transaction.atomic():
            for client in matches:
                old_name = client.first_name.strip()
                client.first_name = client.phone.strip()
                client.phone = old_name
                client.save(update_fields=["first_name", "phone", "updated_at"])
                if client.user_id:
                    client.user.first_name = client.first_name
                    client.user.last_name = client.last_name
                    client.user.phone = client.phone
                    client.user.save(
                        update_fields=["first_name", "last_name", "phone"]
                    )

        self.stdout.write(self.style.SUCCESS(f"Reparados: {len(matches)}"))
        self.stdout.write(f"Copia de seguridad: {backup_path}")
