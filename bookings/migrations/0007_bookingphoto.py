from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0001_initial"),
        ("bookings", "0003_booking_discount_amount_snapshot_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="BookingPhoto",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("image", models.FileField(upload_to="booking_photos/%Y/%m/", verbose_name="Foto")),
                ("photo_type", models.CharField(choices=[("base", "Base"), ("before", "Antes"), ("after", "Después"), ("incident", "Incidencia"), ("follow_up", "Seguimiento")], default="before", max_length=20, verbose_name="Tipo")),
                ("notes", models.TextField(blank=True, verbose_name="Notas")),
                ("is_key_reference", models.BooleanField(default=False, verbose_name="Foto importante")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Creada")),
                ("booking", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="photos", to="bookings.booking", verbose_name="Reserva")),
                ("client", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="booking_photos", to="clients.client", verbose_name="Cliente")),
            ],
            options={
                "verbose_name": "Foto de reserva",
                "verbose_name_plural": "Fotos de reservas",
                "ordering": ["-created_at"],
            },
        ),
    ]
