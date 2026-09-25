from django.db import migrations, models


KIND_CHOICES = [
    ("waitlist_joined", "Waitlist joined"),
    ("waitlist_slot_available", "Waitlist slot available"),
    ("booking_confirmation", "Booking confirmation"),
    ("booking_cancelled", "Booking cancelled"),
    ("booking_rescheduled", "Booking rescheduled"),
    ("reminder_24h", "Reminder 24h"),
    ("reminder_2h", "Reminder 2h"),
    ("reminder_timeout_cancelled", "Reminder timeout cancellation"),
    ("prepayment_request", "Prepayment request"),
    ("prepayment_timeout_cancelled", "Prepayment timeout cancellation"),
    ("password_reset", "Password reset credentials"),
    ("welcome_credentials", "Welcome / login credentials"),
    ("payment_receipt", "Payment receipt"),
    ("birthday_greeting", "Birthday greeting"),
    ("review_request", "Review request"),
    ("client_attending", "Client confirmed attendance"),
    ("client_declined", "Client declined the appointment"),
    ("manual", "Manual"),
]

# Previous defaults of the 24h reminder; the editable DB row is only replaced
# when it still holds one of them, so a text customised in the app is kept.
OLD_REMINDER_BODIES = [
    (
        "Hola {client_name} 👋 Te recordamos tu cita en {salon_name} mañana "
        "{date} a las {time} para {service_name}.\n\n"
        "Si no puedes venir, responde a este mensaje escribiendo una de estas frases:\n"
        "No\nNo voy\nNo quiero\nNo puedo\n\n"
        "Si no respondes, confirmaremos automáticamente tu cita dentro de 30 minutos."
    ),
    (
        "Hola {client_name} 👋 Te recordamos tu cita en {salon_name} mañana "
        "{date} a las {time} para {service_name}.\n\n"
        "Responde a este mensaje con *Sí* o *No*.\n"
        "Si no respondes, confirmaremos automáticamente tu asistencia en 30 minutos."
    ),
]

NEW_REMINDER_BODY = (
    "Hola {client_name} 👋 Mañana {date} a las {time} tienes cita en "
    "{salon_name} ({service_name}).\n\n"
    "¿Vienes?\n"
    "*SÍ* – voy\n"
    "*NO* – no voy"
)

NEW_TEMPLATES = {
    "client_attending": (
        "Gracias tras confirmar asistencia (Sí)",
        "¡Gracias, {client_name}! 💅 Te esperamos el {date} a las {time}.",
    ),
    "client_declined": (
        "Cancelación por el cliente (No) y devolución",
        "Hola {client_name}. Hemos cancelado tu cita del {date} a las {time}.{refund_message}\n"
        "Será un placer verte la próxima vez 💅\n"
        "Reserva cuando quieras: {portal_url}",
    ),
}


def update_templates(apps, schema_editor):
    WhatsAppTemplate = apps.get_model("whatsapp_bot", "WhatsAppTemplate")
    WhatsAppTemplate.objects.filter(
        kind="reminder_24h", body__in=OLD_REMINDER_BODIES
    ).update(body=NEW_REMINDER_BODY)
    for kind, (name, body) in NEW_TEMPLATES.items():
        WhatsAppTemplate.objects.get_or_create(
            kind=kind, defaults={"name": name, "body": body}
        )


class Migration(migrations.Migration):
    dependencies = [("whatsapp_bot", "0010_manual_decline_reminder")]

    operations = [
        migrations.AlterField(
            model_name="whatsappmessage",
            name="kind",
            field=models.CharField(choices=KIND_CHOICES, max_length=40),
        ),
        migrations.AlterField(
            model_name="whatsapptemplate",
            name="kind",
            field=models.CharField(choices=KIND_CHOICES, max_length=40, unique=True),
        ),
        migrations.RunPython(update_templates, migrations.RunPython.noop),
    ]
