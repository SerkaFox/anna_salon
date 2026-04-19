from django.db import models


class Booking(models.Model):
    class Statuses(models.TextChoices):
        PENDING = "pending", "Pendiente"
        CONFIRMED = "confirmed", "Confirmada"
        IN_PROGRESS = "in_progress", "En curso"
        DONE = "done", "Hecha"
        CANCELLED = "cancelled", "Cancelada"
        NO_SHOW = "no_show", "No asistió"

    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.PROTECT,
        related_name="bookings",
        verbose_name="Cliente",
    )
    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        related_name="bookings",
        verbose_name="Empleado",
    )
    service = models.ForeignKey(
        "services_app.Service",
        on_delete=models.PROTECT,
        related_name="bookings",
        verbose_name="Servicio",
    )
    zone = models.ForeignKey(
        "salon.Zone",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="bookings",
        verbose_name="Zona",
    )
    start_at = models.DateTimeField("Inicio")
    end_at = models.DateTimeField("Fin")
    status = models.CharField(
        "Estado",
        max_length=20,
        choices=Statuses.choices,
        default=Statuses.CONFIRMED,
    )
    notes = models.TextField("Notas", blank=True)

    price_snapshot = models.DecimalField("Precio guardado", max_digits=10, decimal_places=2, default=0)
    duration_snapshot = models.PositiveIntegerField("Duración guardada (min)", default=60)

    original_client_price_snapshot = models.DecimalField("Precio original cliente", max_digits=10, decimal_places=2, default=0)
    client_price_snapshot = models.DecimalField("Precio cliente", max_digits=10, decimal_places=2, default=0)
    discount_amount_snapshot = models.DecimalField("Descuento aplicado", max_digits=10, decimal_places=2, default=0)
    referral_reward_applied = models.BooleanField("Premio aplicado", default=False)

    employee_percent_snapshot = models.DecimalField("Porcentaje empleado", max_digits=5, decimal_places=2, default=0)
    employee_amount_snapshot = models.DecimalField("Importe empleado", max_digits=10, decimal_places=2, default=0)
    salon_amount_snapshot = models.DecimalField("Importe salón", max_digits=10, decimal_places=2, default=0)

    created_at = models.DateTimeField("Creada", auto_now_add=True)
    updated_at = models.DateTimeField("Actualizada", auto_now=True)

    class Meta:
        ordering = ["-start_at"]
        verbose_name = "Reserva"
        verbose_name_plural = "Reservas"

    def __str__(self):
        return f"{self.client} · {self.service} · {self.start_at:%d/%m/%Y %H:%M}"