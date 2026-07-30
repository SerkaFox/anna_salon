import csv
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from clients.models import Client


TRUE_VALUES = {"1", "true", "yes", "si", "sí"}


def _text(value):
    return (value or "").strip()


def _bool(value):
    return _text(value).casefold() in TRUE_VALUES


def _date(value):
    value = _text(value)
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _datetime(value):
    value = _text(value)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def _positive_int(value):
    try:
        return max(0, int(Decimal(_text(value) or "0")))
    except (InvalidOperation, ValueError):
        return 0


def _name_key(value):
    return re.sub(r"\s+", " ", _text(value)).casefold()


def _json_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


class Command(BaseCommand):
    help = "Normaliza el CSV de Treatwell, genera JSON e importa los clientes."

    def add_arguments(self, parser):
        parser.add_argument("csv_path", type=Path)
        parser.add_argument(
            "--blacklist",
            type=Path,
            required=True,
        )
        parser.add_argument(
            "--json-output",
            type=Path,
            default=Path("clients/data/treatwell_customers.json"),
        )
        parser.add_argument(
            "--blacklist-output",
            type=Path,
            default=Path("clients/data/treatwell_blacklisted_customers.json"),
        )
        parser.add_argument(
            "--replace",
            action="store_true",
            help=(
                "Elimina clientes y datos operativos vinculados "
                "(reservas, cobros y documentos) antes de importar."
            ),
        )
        parser.add_argument(
            "--json-only",
            action="store_true",
            help="Solo genera los JSON, sin modificar la base de datos.",
        )

    def handle(self, *args, **options):
        csv_path = options["csv_path"].resolve()
        blacklist_path = options["blacklist"].resolve()
        json_output = options["json_output"].resolve()
        blacklist_output = options["blacklist_output"].resolve()

        if not csv_path.exists():
            raise CommandError(f"No existe el CSV: {csv_path}")
        if not blacklist_path.exists():
            raise CommandError(f"No existe la lista negra: {blacklist_path}")

        blacklist_names = json.loads(blacklist_path.read_text(encoding="utf-8"))
        blacklist_keys = {_name_key(name) for name in blacklist_names}

        with csv_path.open("r", encoding="utf-8-sig", newline="") as source:
            rows = list(csv.DictReader(source, delimiter=";"))

        customers = [
            self._normalize(row, blacklist_keys, row_number)
            for row_number, row in enumerate(rows, start=1)
        ]
        blacklisted = [customer for customer in customers if customer["is_blacklisted"]]

        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(
            json.dumps(customers, ensure_ascii=False, indent=2, default=_json_value),
            encoding="utf-8",
        )
        blacklist_output.write_text(
            json.dumps(blacklisted, ensure_ascii=False, indent=2, default=_json_value),
            encoding="utf-8",
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"JSON generado: {len(customers)} clientes; "
                f"{len(blacklisted)} en lista negra."
            )
        )
        if options["json_only"]:
            return

        with transaction.atomic():
            if options["replace"]:
                deleted = self._delete_existing_clients()
                self.stdout.write(f"Registros antiguos eliminados: {deleted}")

            created = 0
            updated = 0
            for customer in customers:
                lookup = self._lookup(customer)
                _client, was_created = Client.objects.update_or_create(
                    **lookup,
                    defaults=customer,
                )
                created += int(was_created)
                updated += int(not was_created)

        self.stdout.write(
            self.style.SUCCESS(
                f"Importacion terminada: {created} creados, {updated} actualizados."
            )
        )

    def _delete_existing_clients(self):
        from accounts.models import User
        from bookings.models import Booking
        from documents.models import FiscalDocument
        from payments.models import Payment as OnlinePayment

        client_ids = list(Client.objects.values_list("pk", flat=True))
        if not client_ids:
            return 0
        linked_user_ids = list(
            Client.objects.exclude(user_id=None).values_list("user_id", flat=True)
        )
        bookings = Booking.objects.filter(client_id__in=client_ids)
        FiscalDocument.objects.filter(booking__in=bookings).delete()
        OnlinePayment.objects.filter(booking__in=bookings).delete()
        bookings.delete()
        deleted, _details = Client.objects.filter(pk__in=client_ids).delete()
        User.objects.filter(
            pk__in=linked_user_ids,
            role=User.ROLE_CLIENT,
        ).delete()
        return deleted

    def _lookup(self, customer):
        if customer["external_id"]:
            return {
                "external_source": "treatwell",
                "external_id": customer["external_id"],
            }
        if customer["phone"]:
            return {"phone": customer["phone"]}
        if customer["email"]:
            return {"email": customer["email"]}
        return {
            "first_name": customer["first_name"],
            "last_name": customer["last_name"],
            "external_source": "treatwell",
        }

    def _normalize(self, row, blacklist_keys, row_number):
        first_name = _text(row.get("firstname"))
        last_name = _text(row.get("lastname"))
        phone = _text(row.get("phonenumber"))
        email = _text(row.get("email"))
        if not first_name and not last_name:
            first_name = phone or email or "Cliente Treatwell"
        full_name_key = _name_key(f"{first_name} {last_name}")

        native_external_id = _text(row.get("externalid"))
        external_id = native_external_id or f"csv-{row_number:06d}"

        return {
            "first_name": first_name,
            "last_name": last_name,
            "phone": phone,
            "alternate_phone": _text(row.get("alternatephonenumber")),
            "email": email,
            "address": _text(row.get("address")),
            "postcode": _text(row.get("zipcode")),
            "fiscal_id": _text(row.get("fiscalcode")),
            "birth_date": _date(row.get("dateofbirth")),
            "gender": _text(row.get("gender")),
            "notes": _text(row.get("notes")),
            "how_we_met": _text(row.get("howwemet")),
            "external_source": "treatwell",
            "external_id": external_id,
            "booking_count": _positive_int(row.get("countbookings")),
            "last_appointment_at": _date(row.get("lastappointmentat")),
            "acquisition_date": _date(row.get("acquisitiondate")),
            "acquired_at": _date(row.get("acquiredat")),
            "average_expense_amount_cents": _positive_int(
                row.get("averageexpenseamountcents")
            ),
            "appointments_frequency": _text(row.get("appointmentsfrequency")),
            "vat_number": _text(row.get("vatnumber")),
            "marketing_communications_accepted_at": _datetime(
                row.get("marketingcommunicationsacceptedat")
            ),
            "marketing_communications_accepted_by_venue": _bool(
                row.get("marketingcommunicationsacceptedbyvenue")
            ),
            "marketing_communications_updated_at": _datetime(
                row.get("marketingcommunicationsupdatedat")
            ),
            "marketing_communications_updated_by_venue": _bool(
                row.get("marketingcommunicationsupdatedbyvenue")
            ),
            "should_receive_marketing_email_notifications": _bool(
                row.get("shouldreceivemarketingemailnotifications")
            ),
            "should_receive_marketing_sms_notifications": _bool(
                row.get("shouldreceivemarketingsmsnotifications")
            ),
            "consent_notification_services": _bool(
                row.get("consensusnotificationservices")
            ),
            "consent_notification_marketing": _bool(
                row.get("consensusnotificationmarketing")
            ),
            "notify_email": _bool(
                row.get("shouldreceivemarketingemailnotifications")
            ),
            "notify_whatsapp": _bool(
                row.get("shouldreceivemarketingsmsnotifications")
            ),
            "is_blacklisted": full_name_key in blacklist_keys,
            "is_active": True,
        }
