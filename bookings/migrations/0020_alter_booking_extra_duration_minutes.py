from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("bookings", "0019_bookingwaitlistentry_desired_date_to"),
    ]

    operations = [
        migrations.AlterField(
            model_name="booking",
            name="extra_duration_minutes",
            field=models.IntegerField(default=0, verbose_name="Ajuste de tiempo (min)"),
        ),
    ]
