from django.core.management.base import BaseCommand, CommandError

from whatsapp_bot import bridge
from whatsapp_bot.models import WhatsAppConnection


class Command(BaseCommand):
    help = "Disconnect a WhatsApp session and force a new QR scan."

    def add_arguments(self, parser):
        parser.add_argument("name", help="Connection name (e.g. main, aura_demo)")

    def handle(self, *args, **options):
        name = options["name"]
        connection, created = WhatsAppConnection.objects.get_or_create(name=name)
        if created:
            self.stdout.write(f"Created new connection '{name}'.")

        try:
            bridge.reset_session(connection)
            self.stdout.write(self.style.SUCCESS(f"Bridge session '{name}' reset OK."))
        except bridge.WhatsAppBridgeError as exc:
            raise CommandError(f"Bridge error: {exc}")

        connection.status = WhatsAppConnection.Statuses.DISCONNECTED
        connection.phone = ""
        connection.last_error = ""
        connection.save(update_fields=["status", "phone", "last_error", "updated_at"])

        from django.conf import settings
        base = getattr(settings, "PUBLIC_BASE_URL", "").rstrip("/")
        self.stdout.write(f"\nQR page: {base}/whatsapp/connect/{name}/\n")
        self.stdout.write("Open the link above in a browser and scan the QR with WhatsApp.")
