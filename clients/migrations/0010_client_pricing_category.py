from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("clients", "0009_client_prepayment_exempt")]

    operations = [
        migrations.AddField(
            model_name="client",
            name="pricing_category",
            field=models.CharField(
                choices=[
                    ("standard", "Normal"),
                    ("complimentary", "Servicio gratuito"),
                ],
                default="standard",
                max_length=20,
                verbose_name="Categoria de precios",
            ),
        ),
    ]
