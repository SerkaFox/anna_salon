from django.conf import settings
from django.core.management.base import BaseCommand

from whatsapp_bot.models import WhatsAppConnection, WhatsAppLoginLink


class Command(BaseCommand):
    help = "Create a one-time WhatsApp QR login link."

    def add_arguments(self, parser):
        parser.add_argument("--connection", default="main")
        parser.add_argument("--ttl-minutes", type=int, default=15)
        parser.add_argument("--note", default="")

    def handle(self, *args, **options):
        connection, _created = WhatsAppConnection.objects.get_or_create(name=options["connection"])
        login = WhatsAppLoginLink.create_for_connection(
            connection,
            ttl_minutes=options["ttl_minutes"],
            note=options["note"],
        )
        base_url = getattr(settings, "PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        url = f"{base_url}/whatsapp/connect/{login.token}/"
        self.stdout.write(url)
