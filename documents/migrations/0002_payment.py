from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0008_booking_source"),
        ("documents", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Payment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("paid_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="Fecha de pago")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=10, verbose_name="Importe")),
                ("method", models.CharField(choices=[("cash", "Efectivo"), ("card", "Tarjeta"), ("bizum", "Bizum"), ("transfer", "Transferencia")], default="card", max_length=20, verbose_name="Método")),
                ("reference", models.CharField(blank=True, max_length=140, verbose_name="Referencia")),
                ("notes", models.TextField(blank=True, verbose_name="Notas")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Creado")),
                ("booking", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payments", to="bookings.booking", verbose_name="Reserva")),
                ("fiscal_document", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="payments", to="documents.fiscaldocument", verbose_name="Documento")),
            ],
            options={
                "verbose_name": "Pago",
                "verbose_name_plural": "Pagos",
                "ordering": ["-paid_at", "-id"],
            },
        ),
    ]
