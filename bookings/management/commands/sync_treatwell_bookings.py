import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from bookings.management.commands.import_treatwell_bookings import (
    Command as ImportCommand,
    Resolver,
    _datetime,
)
from bookings.models import Booking
from bookings.treatwell import TreatwellAPIError, TreatwellClient, normalize_appointment


MADRID = ZoneInfo("Europe/Madrid")


class Command(BaseCommand):
    help = "Sincroniza citas nuevas o modificadas de Treatwell con la base de datos."

    def add_arguments(self, parser):
        parser.add_argument("--from-date", type=date.fromisoformat, default=date.today() - timedelta(days=90))
        parser.add_argument("--to-date", type=date.fromisoformat, default=date.today() + timedelta(days=730))
        parser.add_argument("--chunk-days", type=int, default=31)
        parser.add_argument("--workers", type=int, default=4)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--force", action="store_true")
        parser.add_argument(
            "--report",
            type=Path,
            default=Path("bookings/data/treatwell_sync_report.json"),
        )

    def handle(self, *args, **options):
        email = os.environ.get("TREATWELL_EMAIL", "").strip()
        password = os.environ.get("TREATWELL_PASSWORD", "")
        if not email or not password:
            raise CommandError("Faltan TREATWELL_EMAIL o TREATWELL_PASSWORD")
        if options["from_date"] > options["to_date"]:
            raise CommandError("--from-date no puede ser posterior a --to-date")

        client = TreatwellClient(email, password)
        try:
            client.login()
            stubs = self._index(client, options)
        except TreatwellAPIError as exc:
            raise CommandError(str(exc)) from exc

        known = {
            external_id: updated_at
            for external_id, updated_at in Booking.objects.filter(
                external_source="treatwell",
                external_id__in=stubs,
            ).values_list("external_id", "external_updated_at")
        }
        pending = {}
        for appointment_id, stub in stubs.items():
            remote_updated = _datetime(stub.get("updated_at"))
            local_updated = known.get(appointment_id)
            if (
                options["force"]
                or appointment_id not in known
                or local_updated is None
                or remote_updated is None
                or remote_updated > local_updated
            ):
                pending[appointment_id] = stub

        report = {
            "generated_at": datetime.now(tz=MADRID).isoformat(),
            "mode": "apply" if options["apply"] else "dry-run",
            "indexed": len(stubs),
            "unchanged": len(stubs) - len(pending),
            "pending": len(pending),
            "created": 0,
            "updated": 0,
            "payments_created": 0,
            "clients_created": 0,
            "unmatched": [],
            "errors": [],
        }
        resolver = Resolver()
        with ThreadPoolExecutor(max_workers=max(1, options["workers"])) as executor:
            futures = {
                executor.submit(client.appointment_detail, appointment_id): (appointment_id, stub)
                for appointment_id, stub in pending.items()
            }
            completed = 0
            for future in as_completed(futures):
                appointment_id, stub = futures[future]
                try:
                    detail = future.result()
                    row = normalize_appointment(stub, detail)
                    resolved, client_created = self._resolve(
                        row, resolver, create_client=options["apply"]
                    )
                    report["clients_created"] += int(client_created)
                    if resolved is None:
                        report["unmatched"].append(self._unmatched(row, resolver))
                        continue
                    if options["apply"]:
                        with transaction.atomic():
                            created, payment_created = ImportCommand._save(row, *resolved)
                        report["created" if created else "updated"] += 1
                        report["payments_created"] += int(payment_created)
                except Exception as exc:  # The report must survive one malformed remote row.
                    report["errors"].append(
                        {"external_id": appointment_id, "error": str(exc)[:500]}
                    )
                finally:
                    completed += 1
                    if completed % 25 == 0 or completed == len(pending):
                        self.stdout.write(f"Sincronizadas: {completed}/{len(pending)}")

        report_path = options["report"].resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        self.stdout.write(
            self.style.SUCCESS(
                f"Treatwell: {len(stubs)} en indice, {len(pending)} cambiadas, "
                f"{report['created']} creadas, {report['updated']} actualizadas, "
                f"{len(report['unmatched'])} sin correspondencia."
            )
        )
        if report["errors"]:
            raise CommandError(f"Hubo {len(report['errors'])} errores; consulta {report_path}")

    def _index(self, client, options):
        current = options["from_date"]
        final = options["to_date"]
        result = {}
        while current <= final:
            end = min(final, current + timedelta(days=max(1, options["chunk_days"]) - 1))
            from_time = datetime.combine(current, time.min, tzinfo=MADRID).isoformat()
            to_time = datetime.combine(end, time.max, tzinfo=MADRID).isoformat()
            for stub in client.list_appointments(from_time, to_time):
                if stub.get("id") is not None:
                    result[str(stub["id"])] = stub
            current = end + timedelta(days=1)
        return result

    @staticmethod
    def _resolve(row, resolver, *, create_client=False):
        client, _client_match = resolver.client(row.get("client") or {})
        client_created = False
        if client is None and create_client:
            client = resolver.create_client(row.get("client") or {})
            client_created = client is not None
        employee, _employee_match = resolver.employee(row.get("employee") or {})
        service, _service_match = resolver.service(row.get("service") or {})
        start_at = _datetime(row.get("start_at"))
        end_at = _datetime(row.get("end_at"))
        if None in {client, employee, service, start_at, end_at}:
            return None, client_created
        return (client, employee, service, start_at, end_at), client_created

    @staticmethod
    def _unmatched(row, resolver):
        client, _ = resolver.client(row.get("client") or {})
        employee, _ = resolver.employee(row.get("employee") or {})
        service, _ = resolver.service(row.get("service") or {})
        missing = []
        if client is None:
            missing.append("client")
        if employee is None:
            missing.append("employee")
        if service is None:
            missing.append("service")
        if not _datetime(row.get("start_at")) or not _datetime(row.get("end_at")):
            missing.append("time")
        return {
            "external_id": row.get("external_id"),
            "start_at": row.get("start_at"),
            "treatwell_status": row.get("treatwell_status"),
            "missing": missing,
            "client": (row.get("client") or {}).get("full_name", ""),
            "employee": (row.get("employee") or {}).get("full_name", ""),
            "service": (row.get("service") or {}).get("name", ""),
        }
