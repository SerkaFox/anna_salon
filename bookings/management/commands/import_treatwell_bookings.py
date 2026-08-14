import json
import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from bookings.models import Booking
from clients.models import Client
from employees.models import Employee
from payments.models import Payment
from services_app.models import Service


def _key(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _phone_key(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-9:] if len(digits) >= 9 else digits


def _datetime(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return timezone.make_aware(result) if timezone.is_naive(result) else result


def _decimal(value):
    try:
        return Decimal(str(value or "0")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


class Resolver:
    def __init__(self):
        self.clients = list(Client.objects.all())
        self.employees = list(Employee.objects.all())
        self.services = list(Service.objects.all())

    def client(self, data):
        phone = _phone_key(data.get("phone"))
        if phone:
            matches = [
                item
                for item in self.clients
                if phone in {_phone_key(item.phone), _phone_key(item.alternate_phone)}
            ]
            if len(matches) == 1:
                return matches[0], "phone"
        email = str(data.get("email") or "").strip().casefold()
        if email:
            matches = [item for item in self.clients if item.email.strip().casefold() == email]
            if len(matches) == 1:
                return matches[0], "email"
        name = _key(data.get("full_name") or f'{data.get("first_name", "")} {data.get("last_name", "")}')
        if name:
            matches = [item for item in self.clients if _key(item.full_name) == name]
            if len(matches) == 1:
                return matches[0], "name"
        return None, "unmatched"

    def employee(self, data):
        name = _key(data.get("full_name") or f'{data.get("first_name", "")} {data.get("last_name", "")}')
        matches = [item for item in self.employees if _key(item.full_name) == name]
        if len(matches) == 1:
            return matches[0], "name"
        # Treatwell often omits the surname. A unique first name is still deterministic.
        first_name = _key(data.get("first_name"))
        matches = [item for item in self.employees if first_name and _key(item.first_name) == first_name]
        if len(matches) == 1:
            return matches[0], "first_name"
        return None, "unmatched"

    def service(self, data):
        name = _key(data.get("name"))
        matches = [item for item in self.services if _key(item.name) == name]
        if len(matches) == 1:
            return matches[0], "name"
        return None, "unmatched"


class Command(BaseCommand):
    help = "Valida e importa el JSON normalizado de citas Treatwell de forma idempotente."

    def add_arguments(self, parser):
        parser.add_argument("json_path", type=Path)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--report",
            type=Path,
            default=Path("bookings/data/treatwell_import_report.json"),
        )

    def handle(self, *args, **options):
        path = options["json_path"].resolve()
        if not path.exists():
            raise CommandError(f"No existe el JSON: {path}")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"JSON invalido: {exc}") from exc
        if (document.get("meta") or {}).get("format") != "anna-treatwell-bookings-v1":
            raise CommandError("Formato no compatible; se esperaba anna-treatwell-bookings-v1")

        resolver = Resolver()
        report = {
            "mode": "apply" if options["apply"] else "dry-run",
            "total": 0,
            "ready": 0,
            "created": 0,
            "updated": 0,
            "payments_created": 0,
            "unmatched": [],
        }
        resolved = []
        for row in document.get("appointments") or []:
            report["total"] += 1
            client, client_match = resolver.client(row.get("client") or {})
            employee, employee_match = resolver.employee(row.get("employee") or {})
            service, service_match = resolver.service(row.get("service") or {})
            missing = []
            if client is None:
                missing.append("client")
            if employee is None:
                missing.append("employee")
            if service is None:
                missing.append("service")
            start_at = _datetime(row.get("start_at"))
            end_at = _datetime(row.get("end_at"))
            if start_at is None or end_at is None:
                missing.append("time")
            if missing:
                report["unmatched"].append(
                    {
                        "external_id": row.get("external_id"),
                        "start_at": row.get("start_at"),
                        "missing": missing,
                        "client": (row.get("client") or {}).get("full_name", ""),
                        "employee": (row.get("employee") or {}).get("full_name", ""),
                        "service": (row.get("service") or {}).get("name", ""),
                    }
                )
                continue
            report["ready"] += 1
            resolved.append(
                (row, client, employee, service, start_at, end_at, client_match, employee_match, service_match)
            )

        if options["apply"]:
            with transaction.atomic():
                for row, client, employee, service, start_at, end_at, *_matches in resolved:
                    created, payment_created = self._save(
                        row, client, employee, service, start_at, end_at
                    )
                    report["created" if created else "updated"] += 1
                    report["payments_created"] += int(payment_created)

        report_path = options["report"].resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        action = "Importacion" if options["apply"] else "Validacion"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action}: {report['ready']}/{report['total']} listas; "
                f"{len(report['unmatched'])} sin coincidencia. Informe: {report_path}"
            )
        )

    @staticmethod
    def _save(row, client, employee, service, start_at, end_at):
        price = _decimal(row.get("price"))
        duration = max(1, int(row.get("duration_minutes") or service.duration_minutes))
        employee_percent = employee.commission_percent
        employee_amount = (price * employee_percent / Decimal("100")).quantize(Decimal("0.01"))
        zone = Command._zone(employee, service)
        booking, created = Booking.objects.update_or_create(
            external_source="treatwell",
            external_id=str(row["external_id"]),
            defaults={
                "client": client,
                "employee": employee,
                "service": service,
                "zone": zone,
                "start_at": start_at,
                "end_at": end_at,
                "status": row.get("status") or Booking.Statuses.CONFIRMED,
                "source": Booking.Sources.TREATWELL,
                "notes": row.get("notes") or "",
                "completed_at": _datetime(row.get("checked_out_at")),
                "external_updated_at": _datetime(row.get("updated_at")),
                "price_snapshot": price,
                "duration_snapshot": duration,
                "original_client_price_snapshot": price,
                "client_price_snapshot": price,
                "discount_amount_snapshot": Decimal("0.00"),
                "employee_percent_snapshot": employee_percent,
                "employee_amount_snapshot": employee_amount,
                "salon_amount_snapshot": price - employee_amount,
            },
        )
        paid_amount = _decimal(row.get("paid_online_amount"))
        payment_created = False
        if row.get("paid_online") and paid_amount > 0:
            _payment, payment_created = Payment.objects.update_or_create(
                order_number=f"treatwell-{row['external_id']}",
                defaults={
                    "booking": booking,
                    "amount": paid_amount,
                    "currency": "978",
                    "provider": Payment.Providers.TREATWELL,
                    "method": Payment.Methods.CARD,
                    "status": Payment.Statuses.PAID,
                    "paid_at": _datetime(row.get("paid_online_at")) or start_at,
                    "raw_event": {
                        "source": "treatwell_import",
                        "appointment_id": str(row["external_id"]),
                    },
                },
            )
        return created, payment_created

    @staticmethod
    def _zone(employee, service):
        employee_zones = list(employee.zones.all())
        if len(employee_zones) == 1:
            return employee_zones[0]
        service_zones = list(service.allowed_zones.all())
        shared = [zone for zone in employee_zones if zone in service_zones]
        if len(shared) == 1:
            return shared[0]
        if len(service_zones) == 1:
            return service_zones[0]
        return None
