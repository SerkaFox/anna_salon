import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError

from bookings.treatwell import TreatwellAPIError, TreatwellClient, normalize_appointment


MADRID = ZoneInfo("Europe/Madrid")


class Command(BaseCommand):
    help = "Descarga cada cita de Treatwell y genera un JSON normalizado para importacion."

    def add_arguments(self, parser):
        parser.add_argument("--from-date", type=date.fromisoformat, default=date(2010, 1, 1))
        parser.add_argument(
            "--to-date",
            type=date.fromisoformat,
            default=date.today() + timedelta(days=730),
        )
        parser.add_argument("--chunk-days", type=int, default=31)
        parser.add_argument("--workers", type=int, default=4)
        parser.add_argument("--max-appointments", type=int)
        parser.add_argument(
            "--output",
            type=Path,
            default=Path("bookings/data/treatwell_bookings.json"),
        )
        parser.add_argument(
            "--checkpoint",
            type=Path,
            default=Path("bookings/data/treatwell_bookings.raw.jsonl"),
        )
        parser.add_argument("--no-resume", action="store_true")

    def handle(self, *args, **options):
        email = os.environ.get("TREATWELL_EMAIL", "").strip()
        password = os.environ.get("TREATWELL_PASSWORD", "")
        if not email or not password:
            raise CommandError(
                "Define TREATWELL_EMAIL y TREATWELL_PASSWORD en el entorno; "
                "la contrasena nunca se guarda en el JSON."
            )
        if options["from_date"] > options["to_date"]:
            raise CommandError("--from-date no puede ser posterior a --to-date")
        if options["chunk_days"] < 1 or options["workers"] < 1:
            raise CommandError("--chunk-days y --workers deben ser mayores que cero")

        output = options["output"].resolve()
        checkpoint = options["checkpoint"].resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        if options["no_resume"]:
            checkpoint.unlink(missing_ok=True)

        client = TreatwellClient(email, password)
        try:
            client.login()
            stubs = self._download_index(client, options)
            details = self._download_details(client, stubs, checkpoint, options["workers"])
        except TreatwellAPIError as exc:
            raise CommandError(str(exc)) from exc

        normalized = []
        missing = []
        for appointment_id, stub in stubs.items():
            detail = details.get(appointment_id)
            if detail is None:
                missing.append(appointment_id)
                continue
            normalized.append(normalize_appointment(stub, detail))
        normalized.sort(key=lambda item: (item["start_at"], item["external_id"]))

        document = {
            "meta": {
                "format": "anna-treatwell-bookings-v1",
                "generated_at": datetime.now(tz=MADRID).isoformat(),
                "venue_id": str(client.venue_id),
                "venue_name": client.venue_name,
                "from_date": options["from_date"].isoformat(),
                "to_date": options["to_date"].isoformat(),
                "appointments_count": len(normalized),
                "missing_details": [str(value) for value in missing],
            },
            "appointments": normalized,
        }
        self._atomic_json(output, document)
        if missing:
            raise CommandError(
                f"Faltan {len(missing)} detalles; conserva {checkpoint} y repite el comando."
            )
        self.stdout.write(self.style.SUCCESS(f"Exportadas {len(normalized)} citas a {output}"))

    def _download_index(self, client, options):
        start = options["from_date"]
        final = options["to_date"]
        chunk_days = options["chunk_days"]
        stubs = {}
        while start <= final:
            end = min(final, start + timedelta(days=chunk_days - 1))
            from_time = datetime.combine(start, time.min, tzinfo=MADRID).isoformat()
            to_time = datetime.combine(end, time.max, tzinfo=MADRID).isoformat()
            rows = client.list_appointments(from_time, to_time)
            for row in rows:
                if row.get("id") is not None:
                    stubs[str(row["id"])] = row
            self.stdout.write(
                f"Indice {start.isoformat()}..{end.isoformat()}: {len(rows)} "
                f"({len(stubs)} unicas)"
            )
            start = end + timedelta(days=1)
        if options["max_appointments"] is not None:
            limit = max(0, options["max_appointments"])
            stubs = dict(list(stubs.items())[:limit])
        return stubs

    def _download_details(self, client, stubs, checkpoint, workers):
        details = self._read_checkpoint(checkpoint)
        pending = [appointment_id for appointment_id in stubs if appointment_id not in details]
        self.stdout.write(
            f"Detalles: {len(details)} recuperados, {len(pending)} pendientes; "
            f"{workers} trabajadores."
        )
        if not pending:
            return details
        with checkpoint.open("a", encoding="utf-8", newline="\n") as target:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(client.appointment_detail, appointment_id): appointment_id
                    for appointment_id in pending
                }
                completed = 0
                for future in as_completed(futures):
                    appointment_id = futures[future]
                    detail = future.result()
                    record = {"appointment_id": appointment_id, "detail": detail}
                    target.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                    target.flush()
                    details[appointment_id] = detail
                    completed += 1
                    if completed % 25 == 0 or completed == len(pending):
                        self.stdout.write(f"Detalles descargados: {completed}/{len(pending)}")
        return details

    @staticmethod
    def _read_checkpoint(path):
        result = {}
        if not path.exists():
            return result
        with path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    # A power loss can leave only the last JSONL line incomplete.
                    continue
                appointment_id = str(record.get("appointment_id") or "")
                if appointment_id and isinstance(record.get("detail"), dict):
                    result[appointment_id] = record["detail"]
        return result

    @staticmethod
    def _atomic_json(path, data):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
