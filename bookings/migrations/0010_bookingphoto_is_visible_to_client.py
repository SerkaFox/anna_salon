from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0009_alter_booking_source_employee"),
    ]

    operations = [
        migrations.AddField(
            model_name="bookingphoto",
            name="is_visible_to_client",
            field=models.BooleanField(
                default=False,
                verbose_name="Visible para cliente y empleado",
            ),
        ),
    ]
