# Generated manually for the documents app.

from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q
from django.utils import timezone


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("bookings", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="FiscalDocument",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("document_type", models.CharField(choices=[("receipt", "Recibo"), ("invoice", "Factura"), ("proforma", "Proforma")], default="receipt", max_length=20, verbose_name="Tipo")),
                ("status", models.CharField(choices=[("draft", "Borrador"), ("issued", "Emitido"), ("cancelled", "Cancelado")], default="issued", max_length=20, verbose_name="Estado")),
                ("number", models.CharField(blank=True, max_length=40, unique=True, verbose_name="Número")),
                ("issue_date", models.DateField(default=timezone.localdate, verbose_name="Fecha de emisión")),
                ("tax_rate", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=5, verbose_name="IVA %")),
                ("subtotal_amount", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=10, verbose_name="Base imponible")),
                ("tax_amount", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=10, verbose_name="IVA")),
                ("total_amount", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=10, verbose_name="Total")),
                ("notes", models.TextField(blank=True, verbose_name="Notas")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Creado")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Actualizado")),
                ("booking", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="fiscal_documents", to="bookings.booking", verbose_name="Reserva")),
            ],
            options={
                "verbose_name": "Documento fiscal",
                "verbose_name_plural": "Documentos fiscales",
                "ordering": ["-issue_date", "-id"],
            },
        ),
        migrations.AddConstraint(
            model_name="fiscaldocument",
            constraint=models.UniqueConstraint(condition=Q(("status__in", ["draft", "issued"])), fields=("booking", "document_type"), name="unique_active_document_per_booking_type"),
        ),
    ]
