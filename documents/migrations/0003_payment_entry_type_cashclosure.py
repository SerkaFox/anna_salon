from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0002_payment"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="entry_type",
            field=models.CharField(choices=[("payment", "Pago"), ("refund", "Devolución")], default="payment", max_length=20, verbose_name="Tipo movimiento"),
        ),
        migrations.CreateModel(
            name="CashClosure",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("closure_date", models.DateField(unique=True, verbose_name="Fecha")),
                ("total_amount", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=10, verbose_name="Total")),
                ("cash_amount", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=10, verbose_name="Efectivo")),
                ("card_amount", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=10, verbose_name="Tarjeta")),
                ("bizum_amount", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=10, verbose_name="Bizum")),
                ("transfer_amount", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=10, verbose_name="Transferencia")),
                ("payments_count", models.PositiveIntegerField(default=0, verbose_name="Movimientos")),
                ("notes", models.TextField(blank=True, verbose_name="Notas")),
                ("closed_at", models.DateTimeField(auto_now_add=True, verbose_name="Cerrado el")),
                ("closed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="cash_closures", to=settings.AUTH_USER_MODEL, verbose_name="Cerrado por")),
            ],
            options={
                "verbose_name": "Cierre de caja",
                "verbose_name_plural": "Cierres de caja",
                "ordering": ["-closure_date", "-id"],
            },
        ),
    ]
