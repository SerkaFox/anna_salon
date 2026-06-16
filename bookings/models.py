from django.db import models
from django.utils import timezone


class Booking(models.Model):
    class Statuses(models.TextChoices):
        PENDING = "pending", "Pendiente"
        CONFIRMED = "confirmed", "Confirmada"
        IN_PROGRESS = "in_progress", "En curso"
        DONE = "done", "Hecha"
        CANCELLED = "cancelled", "Cancelada"
        NO_SHOW = "no_show", "No asistió"

    class Sources(models.TextChoices):
        MANUAL = "manual", "Manual"
        WEBSITE = "website", "Sitio web"
        WHATSAPP = "whatsapp", "WhatsApp"
        INSTAGRAM = "instagram", "Instagram"
        PHONE = "phone", "Teléfono"
        WALK_IN = "walk_in", "En el salón"
        REBOOKING = "rebooking", "Cliente recurrente"
        REFERRAL = "referral", "Recomendación"
        EMPLOYEE = "employee", "Empleado"
        GOOGLE = "google", "Google / Maps"
        OTHER = "other", "Otro"

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
    source = models.CharField(
        "Origen",
        max_length=20,
        choices=Sources.choices,
        default=Sources.MANUAL,
    )
    notes = models.TextField("Notas", blank=True)

    price_snapshot = models.DecimalField("Precio guardado", max_digits=10, decimal_places=2, default=0)
    duration_snapshot = models.PositiveIntegerField("Duración guardada (min)", default=60)

    original_client_price_snapshot = models.DecimalField("Precio original cliente", max_digits=10, decimal_places=2, default=0)
    client_price_snapshot = models.DecimalField("Precio cliente", max_digits=10, decimal_places=2, default=0)
    discount_amount_snapshot = models.DecimalField("Descuento aplicado", max_digits=10, decimal_places=2, default=0)
    referral_reward_applied = models.BooleanField("Premio aplicado", default=False)
    reward_rule = models.ForeignKey(
        "clients.ClientRewardRule",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bookings",
        verbose_name="Premio aplicado",
    )

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

    @property
    def latest_online_payment(self):
        payments = list(getattr(self, "_prefetched_objects_cache", {}).get("online_payments", self.online_payments.all()))
        return payments[0] if payments else None

    @property
    def paid_amount(self):
        return sum(
            (payment.amount for payment in self.online_payments.all() if payment.status == payment.Statuses.PAID),
            0,
        )

    @property
    def payment_status(self):
        latest_payment = self.latest_online_payment
        return latest_payment.status if latest_payment else ""

    @property
    def is_paid(self):
        return self.paid_amount > 0


class BookingPhoto(models.Model):
    class PhotoTypes(models.TextChoices):
        BASE = "base", "Base"
        BEFORE = "before", "Antes"
        AFTER = "after", "Después"
        INCIDENT = "incident", "Incidencia"
        FOLLOW_UP = "follow_up", "Seguimiento"

    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        related_name="photos",
        verbose_name="Reserva",
    )
    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.CASCADE,
        related_name="booking_photos",
        verbose_name="Cliente",
    )
    image = models.FileField("Foto", upload_to="booking_photos/%Y/%m/")
    photo_type = models.CharField(
        "Tipo",
        max_length=20,
        choices=PhotoTypes.choices,
        default=PhotoTypes.BEFORE,
    )
    notes = models.TextField("Notas", blank=True)
    is_key_reference = models.BooleanField("Foto importante", default=False)
    is_visible_to_client = models.BooleanField("Visible para cliente y empleado", default=False)
    created_at = models.DateTimeField("Creada", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Foto de reserva"
        verbose_name_plural = "Fotos de reservas"

    def save(self, *args, **kwargs):
        if self.booking_id:
            self.client = self.booking.client
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_photo_type_display()} · {self.client} · {self.created_at:%d/%m/%Y %H:%M}"


class BookingPrepayment(models.Model):
    class Statuses(models.TextChoices):
        PAID = "paid", "Pagada"
        REFUNDED = "refunded", "Devuelta"
        FORFEITED = "forfeited", "Para salon"

    booking = models.OneToOneField(
        Booking,
        on_delete=models.CASCADE,
        related_name="prepayment",
        verbose_name="Reserva",
    )
    payment = models.OneToOneField(
        "payments.Payment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="booking_prepayment",
        verbose_name="Pago online",
    )
    amount = models.DecimalField("Importe de prepago", max_digits=10, decimal_places=2)
    status = models.CharField("Estado", max_length=20, choices=Statuses.choices, default=Statuses.PAID)
    refundable_until = models.DateTimeField("Devolucion disponible hasta")
    refunded_at = models.DateTimeField("Devuelto el", null=True, blank=True)
    forfeited_at = models.DateTimeField("Asignado al salon el", null=True, blank=True)
    notes = models.TextField("Notas", blank=True)
    created_at = models.DateTimeField("Creado", auto_now_add=True)
    updated_at = models.DateTimeField("Actualizado", auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Prepago de reserva"
        verbose_name_plural = "Prepagos de reservas"

    @property
    def is_refundable(self):
        return self.status == self.Statuses.PAID and timezone.now() <= self.refundable_until

    @property
    def is_forfeitable(self):
        return self.status == self.Statuses.PAID and timezone.now() > self.refundable_until

    def refresh_forfeit_status(self):
        if self.is_forfeitable:
            self.status = self.Statuses.FORFEITED
            self.forfeited_at = timezone.now()
            self.save(update_fields=["status", "forfeited_at", "updated_at"])

    def __str__(self):
        return f"{self.booking} · {self.amount} EUR · {self.get_status_display()}"


class BookingWaitlistEntry(models.Model):
    class Statuses(models.TextChoices):
        ACTIVE = "active", "Activa"
        NOTIFIED = "notified", "Notificada"
        BOOKED = "booked", "Convertida en reserva"
        CANCELLED = "cancelled", "Cancelada"

    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="waitlist_entries",
        verbose_name="Cliente",
    )
    service = models.ForeignKey(
        "services_app.Service",
        on_delete=models.PROTECT,
        related_name="waitlist_entries",
        verbose_name="Servicio",
    )
    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        related_name="waitlist_entries",
        verbose_name="Empleado",
    )
    desired_date = models.DateField("Fecha deseada")
    time_range = models.CharField("Rango horario", max_length=20, blank=True)
    name = models.CharField("Nombre", max_length=180)
    phone = models.CharField("Telefono", max_length=40, blank=True)
    email = models.EmailField("Email", blank=True)
    status = models.CharField("Estado", max_length=20, choices=Statuses.choices, default=Statuses.ACTIVE)
    source = models.CharField("Origen", max_length=20, default=Booking.Sources.WEBSITE)
    notes = models.TextField("Notas", blank=True)
    notified_at = models.DateTimeField("Notificada el", null=True, blank=True)
    created_at = models.DateTimeField("Creada", auto_now_add=True)
    updated_at = models.DateTimeField("Actualizada", auto_now=True)

    class Meta:
        ordering = ["desired_date", "created_at"]
        verbose_name = "Entrada en lista de espera"
        verbose_name_plural = "Lista de espera"

    def __str__(self):
        return f"{self.name} · {self.employee} · {self.desired_date:%d/%m/%Y}"
