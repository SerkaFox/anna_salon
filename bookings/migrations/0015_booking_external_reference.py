from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("bookings", "0014_booking_completed_at")]

    operations = [
        migrations.AddField(
            model_name="booking",
            name="external_id",
            field=models.CharField(blank=True, max_length=120, verbose_name="ID externo"),
        ),
        migrations.AddField(
            model_name="booking",
            name="external_source",
            field=models.CharField(blank=True, max_length=40, verbose_name="Origen externo"),
        ),
        migrations.AddField(
            model_name="booking",
            name="external_updated_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Actualizado en origen"),
        ),
        migrations.AlterField(
            model_name="booking",
            name="source",
            field=models.CharField(
                choices=[
                    ("manual", "Manual"),
                    ("website", "Sitio web"),
                    ("whatsapp", "WhatsApp"),
                    ("instagram", "Instagram"),
                    ("phone", "Teléfono"),
                    ("walk_in", "En el salón"),
                    ("rebooking", "Cliente recurrente"),
                    ("referral", "Recomendación"),
                    ("employee", "Empleado"),
                    ("treatwell", "Treatwell"),
                    ("google", "Google / Maps"),
                    ("other", "Otro"),
                ],
                default="manual",
                max_length=20,
                verbose_name="Origen",
            ),
        ),
        migrations.AddConstraint(
            model_name="booking",
            constraint=models.UniqueConstraint(
                condition=~models.Q(external_id=""),
                fields=("external_source", "external_id"),
                name="unique_booking_external_reference",
            ),
        ),
    ]
