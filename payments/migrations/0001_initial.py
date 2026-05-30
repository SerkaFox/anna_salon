from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("bookings", "0011_booking_reward_rule"),
    ]

    operations = [
        migrations.CreateModel(
            name="Payment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=10, verbose_name="Importe")),
                ("currency", models.CharField(default="978", max_length=3, verbose_name="Moneda")),
                ("order_number", models.CharField(max_length=12, unique=True, verbose_name="Pedido Redsys")),
                ("provider", models.CharField(choices=[("redsys", "Redsys")], default="redsys", max_length=20, verbose_name="Proveedor")),
                ("method", models.CharField(choices=[("card", "Tarjeta"), ("bizum", "Bizum"), ("unknown", "Desconocido")], default="unknown", max_length=20, verbose_name="Método")),
                ("status", models.CharField(choices=[("pending", "Pendiente"), ("paid", "Pagado"), ("failed", "Fallido"), ("cancelled", "Cancelado"), ("refunded", "Devuelto")], default="pending", max_length=20, verbose_name="Estado")),
                ("redsys_response_code", models.CharField(blank=True, max_length=10, verbose_name="Código Redsys")),
                ("redsys_authorisation_code", models.CharField(blank=True, max_length=20, verbose_name="Autorización Redsys")),
                ("raw_request", models.JSONField(blank=True, default=dict, verbose_name="Petición Redsys")),
                ("raw_response", models.JSONField(blank=True, default=dict, verbose_name="Respuesta Redsys")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Creado")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Actualizado")),
                ("paid_at", models.DateTimeField(blank=True, null=True, verbose_name="Pagado el")),
                ("booking", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="online_payments", to="bookings.booking", verbose_name="Reserva")),
            ],
            options={
                "verbose_name": "Pago online",
                "verbose_name_plural": "Pagos online",
                "ordering": ["-created_at", "-id"],
            },
        ),
    ]
