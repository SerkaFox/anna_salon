from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("clients", "0008_client_acquired_at_client_acquisition_date_and_more")]

    operations = [
        migrations.AddField(
            model_name="client",
            name="prepayment_exempt",
            field=models.BooleanField(
                default=False,
                verbose_name="No requiere prepago; paga en el salon",
            ),
        ),
    ]
