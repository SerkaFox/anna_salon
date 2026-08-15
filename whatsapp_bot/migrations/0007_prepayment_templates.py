from django.db import migrations, models


KINDS = [
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
    ("welcome_credentials", "Welcome / login credentials"),
    ("payment_receipt", "Payment receipt"),
    ("birthday_greeting", "Birthday greeting"),
    ("review_request", "Review request"),
    ("manual", "Manual"),
]


def create_templates(apps, schema_editor):
    template_model = apps.get_model("whatsapp_bot", "WhatsAppTemplate")
    templates = {
        "prepayment_request": (
            "Solicitud de prepago",
            "Hola {client_name}. Para confirmar tu cita en {salon_name} del {date} "
            "a las {time} ({service_name}), paga {payment_amount} EUR en los próximos "
            "30 minutos:\n{payment_url}\n\nSi no se recibe el pago antes de "
            "{payment_deadline}, la cita se cancelará automáticamente.",
        ),
        "prepayment_timeout_cancelled": (
            "Cancelación automática sin prepago",
            "Hola {client_name}. No recibimos el prepago en 30 minutos y tu cita en "
            "{salon_name} del {date} a las {time} ({service_name}) se ha cancelado "
            "automáticamente. No se ha realizado ningún cargo.",
        ),
    }
    for kind, (name, body) in templates.items():
        template_model.objects.get_or_create(
            kind=kind,
            defaults={"name": name, "body": body, "enabled": True},
        )


class Migration(migrations.Migration):
    dependencies = [("whatsapp_bot", "0006_reminder_timeout_cancelled")]

    operations = [
        migrations.AlterField(
            model_name="whatsappmessage",
            name="kind",
            field=models.CharField(choices=KINDS, max_length=40),
        ),
        migrations.AlterField(
            model_name="whatsapptemplate",
            name="kind",
            field=models.CharField(choices=KINDS, max_length=40, unique=True),
        ),
        migrations.RunPython(create_templates, migrations.RunPython.noop),
    ]
