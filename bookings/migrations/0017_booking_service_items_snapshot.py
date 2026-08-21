from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("bookings", "0016_booking_prepayment_request")]

    operations = [
        migrations.AddField(
            model_name="booking",
            name="service_items_snapshot",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Snapshot de los servicios incluidos en una sola reserva.",
                verbose_name="Servicios guardados",
            ),
        ),
    ]
