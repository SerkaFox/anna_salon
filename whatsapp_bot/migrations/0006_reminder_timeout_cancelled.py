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
    ("welcome_credentials", "Welcome / login credentials"),
    ("payment_receipt", "Payment receipt"),
    ("birthday_greeting", "Birthday greeting"),
    ("review_request", "Review request"),
    ("manual", "Manual"),
]


def create_timeout_template(apps, schema_editor):
    template_model = apps.get_model("whatsapp_bot", "WhatsAppTemplate")
    template_model.objects.get_or_create(
        kind="reminder_timeout_cancelled",
        defaults={
            "name": "Cancelación automática sin respuesta",
            "body": (
                "Hola {client_name}. Como no recibimos respuesta en 15 minutos, tu cita en "
                "{salon_name} del {date} a las {time} ({service_name}) se ha cancelado "
                "automáticamente. {refund_message}"
            ),
            "enabled": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [("whatsapp_bot", "0005_whatsapptemplate_delay_minutes_and_more")]

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
        migrations.RunPython(create_timeout_template, migrations.RunPython.noop),
    ]
