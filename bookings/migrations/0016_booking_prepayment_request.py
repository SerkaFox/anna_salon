from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("bookings", "0015_booking_external_reference")]

    operations = [
        migrations.AddField(
            model_name="booking",
            name="prepayment_policy",
            field=models.CharField(
                choices=[
                    ("optional", "Sin solicitud de prepago"),
                    ("required", "Prepago obligatorio"),
                    ("exempt", "Sin prepago; paga en el salon"),
                ],
                default="optional",
                max_length=20,
                verbose_name="Politica de prepago",
            ),
        ),
        migrations.AddField(
            model_name="booking",
            name="prepayment_requested_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Prepago solicitado el"),
        ),
        migrations.AddField(
            model_name="booking",
            name="prepayment_deadline_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Limite para el prepago"),
        ),
    ]
