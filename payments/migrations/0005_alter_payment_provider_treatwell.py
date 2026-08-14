from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0004_stripepayoutrequest")]

    operations = [
        migrations.AlterField(
            model_name="payment",
            name="provider",
            field=models.CharField(
                choices=[
                    ("redsys", "Redsys"),
                    ("stripe", "Stripe"),
                    ("treatwell", "Treatwell"),
                ],
                default="redsys",
                max_length=30,
                verbose_name="Proveedor",
            ),
        )
    ]
