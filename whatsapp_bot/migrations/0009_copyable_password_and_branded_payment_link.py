from django.db import migrations


OLD_PASSWORD = (
    "Hola {client_name}. Hemos creado un acceso temporal para {salon_name}.\n\n"
    "Usuario: {username}\nContraseña temporal: {password}\n\n"
    "Acceso: {login_url}\n\nDespués de entrar, cambia la contraseña desde tu perfil."
)
NEW_PASSWORD = (
    "Hola {client_name}. Hemos creado un acceso temporal para {salon_name}.\n\n"
    "Usuario: {username}\nAcceso: {login_url}\n\n"
    "Después de entrar, cambia la contraseña desde tu perfil."
)
OLD_PREPAYMENT = (
    "Hola {client_name}. Para confirmar tu cita en {salon_name} del {date} "
    "a las {time} ({service_name}), paga {payment_amount} EUR en los próximos "
    "30 minutos:\n{payment_url}\n\nSi no se recibe el pago antes de "
    "{payment_deadline}, la cita se cancelará automáticamente."
)
NEW_PREPAYMENT = (
    "Hola {client_name}. Para confirmar tu cita en {salon_name} del {date} "
    "a las {time} ({service_name}), paga {payment_amount} EUR en los próximos "
    "30 minutos:\n👉 Pagar reserva: {payment_url}\n\nSi no se recibe el pago antes de "
    "{payment_deadline}, la cita se cancelará automáticamente."
)


def update_default_templates(apps, schema_editor):
    template = apps.get_model("whatsapp_bot", "WhatsAppTemplate")
    template.objects.filter(kind="password_reset", body=OLD_PASSWORD).update(
        body=NEW_PASSWORD
    )
    template.objects.filter(kind="prepayment_request", body=OLD_PREPAYMENT).update(
        body=NEW_PREPAYMENT
    )


def restore_default_templates(apps, schema_editor):
    template = apps.get_model("whatsapp_bot", "WhatsAppTemplate")
    template.objects.filter(kind="password_reset", body=NEW_PASSWORD).update(
        body=OLD_PASSWORD
    )
    template.objects.filter(kind="prepayment_request", body=NEW_PREPAYMENT).update(
        body=OLD_PREPAYMENT
    )


class Migration(migrations.Migration):
    dependencies = [("whatsapp_bot", "0008_alter_whatsappmessage_kind_and_more")]

    operations = [
        migrations.RunPython(update_default_templates, restore_default_templates),
    ]
