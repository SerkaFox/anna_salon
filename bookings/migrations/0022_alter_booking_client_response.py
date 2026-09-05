from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("bookings", "0021_merge_20260825_2058"),
    ]

    operations = [
        migrations.AlterField(
            model_name="booking",
            name="client_response",
            field=models.CharField(
                choices=[
                    ("pending", "Sin respuesta"),
                    ("attending", "Asistira"),
                    ("cancellation_pending", "Cancelacion pendiente de confirmar"),
                    ("declined", "No asistira"),
                ],
                default="pending",
                max_length=20,
                verbose_name="Respuesta del cliente",
            ),
        ),
    ]
