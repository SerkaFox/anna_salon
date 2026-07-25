from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0004_fiscaldocumentline"),
    ]

    operations = [
        migrations.AddField(
            model_name="cashclosure",
            name="cash_difference",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                max_digits=10,
                verbose_name="Diferencia de efectivo",
            ),
        ),
        migrations.AddField(
            model_name="cashclosure",
            name="declared_cash_amount",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                max_digits=10,
                verbose_name="Efectivo contado",
            ),
        ),
    ]
