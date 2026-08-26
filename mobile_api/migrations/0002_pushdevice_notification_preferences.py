from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("mobile_api", "0001_pushdevice")]

    operations = [
        migrations.AddField(
            model_name="pushdevice",
            name="notify_new_booking",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="pushdevice",
            name="notify_booking_cancelled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="pushdevice",
            name="notify_booking_rescheduled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="pushdevice",
            name="notify_employee_changed",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="pushdevice",
            name="notify_prepayment_received",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="pushdevice",
            name="notify_reminder_24h",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="pushdevice",
            name="notify_reminder_2h",
            field=models.BooleanField(default=True),
        ),
    ]
