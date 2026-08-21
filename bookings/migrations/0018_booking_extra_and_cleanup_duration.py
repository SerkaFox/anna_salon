from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("bookings", "0017_booking_service_items_snapshot")]

    operations = [
        migrations.AddField(
            model_name="booking",
            name="extra_duration_minutes",
            field=models.PositiveIntegerField(
                default=0, verbose_name="Tiempo adicional (min)"
            ),
        ),
        migrations.AddField(
            model_name="booking",
            name="cleanup_duration_minutes",
            field=models.PositiveIntegerField(
                default=0, verbose_name="Tiempo de limpieza (min)"
            ),
        ),
    ]
