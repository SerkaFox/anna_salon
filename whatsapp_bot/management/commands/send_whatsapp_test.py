from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from whatsapp_bot.models import WhatsAppMessage
from whatsapp_bot.services import get_default_connection, normalize_whatsapp_phone, send_whatsapp_message


class Command(BaseCommand):
    help = "Send a manual WhatsApp test message through the configured bridge."

    def add_arguments(self, parser):
        parser.add_argument("phone")
        parser.add_argument("message")

    def handle(self, *args, **options):
        phone = normalize_whatsapp_phone(options["phone"])
        if not phone:
            raise CommandError("Phone is empty or invalid.")
        message = WhatsAppMessage.objects.create(
            connection=get_default_connection(),
            kind=WhatsAppMessage.Kinds.MANUAL,
            to_phone=phone,
            body=options["message"],
            scheduled_for=timezone.now(),
        )
        message = send_whatsapp_message(message)
        self.stdout.write(f"{message.status}: {message.to_phone} ({message.provider_message_id or message.error})")
