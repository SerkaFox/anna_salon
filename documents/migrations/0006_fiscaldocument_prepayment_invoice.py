from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("documents", "0005_cashclosure_declared_cash")]

    operations = [
        migrations.RemoveConstraint(
            model_name="fiscaldocument",
            name="unique_active_document_per_booking_type",
        ),
        migrations.AddField(
            model_name="fiscaldocument",
            name="purpose",
            field=models.CharField(
                choices=[
                    ("standard", "Documento de la reserva"),
                    ("prepayment", "Factura de anticipo"),
                ],
                default="standard",
                max_length=20,
                verbose_name="Finalidad",
            ),
        ),
        migrations.AddField(model_name="fiscaldocument", name="billing_name", field=models.CharField(blank=True, max_length=255, verbose_name="Nombre fiscal")),
        migrations.AddField(model_name="fiscaldocument", name="billing_fiscal_id", field=models.CharField(blank=True, max_length=40, verbose_name="NIE/NIF/CIF fiscal")),
        migrations.AddField(model_name="fiscaldocument", name="billing_address", field=models.CharField(blank=True, max_length=255, verbose_name="Dirección fiscal")),
        migrations.AddField(model_name="fiscaldocument", name="billing_city", field=models.CharField(blank=True, max_length=120, verbose_name="Ciudad fiscal")),
        migrations.AddField(model_name="fiscaldocument", name="billing_postcode", field=models.CharField(blank=True, max_length=20, verbose_name="Código postal fiscal")),
        migrations.AddField(model_name="fiscaldocument", name="billing_email", field=models.EmailField(blank=True, max_length=254, verbose_name="Email de facturación")),
        migrations.AddField(model_name="fiscaldocument", name="billing_phone", field=models.CharField(blank=True, max_length=30, verbose_name="Teléfono de facturación")),
        migrations.AddField(model_name="fiscaldocument", name="online_paid_amount", field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=10, verbose_name="Importe online vinculado")),
        migrations.AddField(model_name="fiscaldocument", name="external_payment_reference", field=models.CharField(blank=True, max_length=255, verbose_name="Referencia del pago online")),
        migrations.AddConstraint(
            model_name="fiscaldocument",
            constraint=models.UniqueConstraint(
                condition=models.Q(status__in=["draft", "issued"]),
                fields=("booking", "document_type", "purpose"),
                name="unique_active_document_per_booking_type_purpose",
            ),
        ),
    ]
