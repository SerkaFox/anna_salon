from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("bookings", "0022_alter_booking_client_response")]
    operations = [migrations.AddField(
        model_name="booking", name="cancelled_at",
        field=models.DateTimeField("Cancelada", null=True, blank=True, db_index=True),
    )]
