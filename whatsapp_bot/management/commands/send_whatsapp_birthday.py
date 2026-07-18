from datetime import date

from django.core.management.base import BaseCommand

from clients.models import Client
from whatsapp_bot.services import queue_birthday_greeting, send_whatsapp_message


class Command(BaseCommand):
    help = "Send birthday WhatsApp greetings to clients whose birthday is today."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Queue only, do not send.")

    def handle(self, *args, **options):
        today = date.today()
        clients = Client.objects.filter(
            birth_date__month=today.month,
            birth_date__day=today.day,
            is_active=True,
        ).exclude(phone="")

        sent = skipped = errors = 0
        for client in clients:
            try:
                msg, created = queue_birthday_greeting(client)
                if not created:
                    skipped += 1
                    continue
                if not options["dry_run"] and msg:
                    send_whatsapp_message(msg)
                sent += 1
            except Exception as exc:
                self.stderr.write(f"Error for {client}: {exc}")
                errors += 1

        self.stdout.write(
            f"Birthday greetings: sent={sent} skipped={skipped} errors={errors} "
            f"(today: {today.day}/{today.month})"
        )
