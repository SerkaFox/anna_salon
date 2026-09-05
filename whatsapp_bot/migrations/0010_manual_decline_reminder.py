from django.db import migrations


REMINDER_BODY = (
    "Hola {client_name} 👋 Te recordamos tu cita en {salon_name} mañana "
    "{date} a las {time} para {service_name}.\n\n"
    "Si no puedes venir, responde a este mensaje escribiendo una de estas frases:\n"
    "No\nNo voy\nNo quiero\nNo puedo\n\n"
    "Si no respondes, confirmaremos automáticamente tu cita dentro de 30 minutos."
)


def update_reminder(apps, schema_editor):
    WhatsAppTemplate = apps.get_model("whatsapp_bot", "WhatsAppTemplate")
    WhatsAppTemplate.objects.filter(kind="reminder_24h").update(body=REMINDER_BODY)
    WhatsAppTemplate.objects.filter(kind="reminder_timeout_cancelled").update(
        name="Obsoleto: cancelación por falta de respuesta",
        enabled=False,
    )


class Migration(migrations.Migration):
    dependencies = [("whatsapp_bot", "0009_copyable_password_and_branded_payment_link")]

    operations = [migrations.RunPython(update_reminder, migrations.RunPython.noop)]
