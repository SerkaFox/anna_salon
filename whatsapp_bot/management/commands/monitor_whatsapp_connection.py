from django.core.management.base import BaseCommand

from whatsapp_bot.monitoring import refresh_connection_status


class Command(BaseCommand):
    help = "Refresh the real WhatsApp bridge connection state."

    def add_arguments(self, parser):
        parser.add_argument("--session", default="main")

    def handle(self, *args, **options):
        result = refresh_connection_status(options["session"])
        self.stdout.write(
            f"status={result['status']} connected={result['connected']} "
            f"checked_at={result['last_checked_at']}"
        )
