from django.core.management.base import BaseCommand

from whatsapp_bot.monitoring import refresh_connection_status


class Command(BaseCommand):
    help = "Refresh the real WhatsApp bridge connection state."

    def add_arguments(self, parser):
        parser.add_argument("--session", default="main")
        parser.add_argument("--alert", action="store_true")

    def handle(self, *args, **options):
        result = refresh_connection_status(options["session"])
        if options["alert"]:
            from whatsapp_bot.connection_alerts import check_connection_alert
            sent = check_connection_alert(result)
            if sent:
                self.stdout.write(f"Owner outage alert sent to {sent} device(s)")
        self.stdout.write(
            f"status={result['status']} connected={result['connected']} "
            f"checked_at={result['last_checked_at']}"
        )
