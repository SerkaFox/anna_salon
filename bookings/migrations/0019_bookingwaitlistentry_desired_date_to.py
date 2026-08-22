from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("bookings", "0018_booking_extra_and_cleanup_duration"),
    ]

    operations = [
        migrations.AddField(
            model_name="bookingwaitlistentry",
            name="desired_date_to",
            field=models.DateField(blank=True, null=True, verbose_name="Fecha deseada hasta"),
        ),
    ]
