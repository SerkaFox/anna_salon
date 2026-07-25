from django.db import models
from django.core.validators import MaxValueValidator, MinValueValidator


class SalonSettings(models.Model):
    deposit_percent = models.DecimalField(
        "Porcentaje de prepago",
        max_digits=5,
        decimal_places=2,
        default=10,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    receipt_business_name = models.CharField(
        "Nombre en el recibo",
        max_length=160,
        default="BRIMOON Studio",
    )
    receipt_address = models.CharField(
        "Direccion en el recibo",
        max_length=255,
        default="Rafaela Ybarra Kalea, 2 bis, Deusto, 48014 Bilbao, Bizkaia",
    )
    receipt_phone = models.CharField(
        "Telefono en el recibo",
        max_length=40,
        default="643996431",
        blank=True,
    )
    receipt_email = models.EmailField(
        "Email en el recibo",
        blank=True,
    )
    receipt_website = models.URLField(
        "Web en el recibo",
        default="https://brimoon.es",
        blank=True,
    )
    receipt_footer = models.CharField(
        "Mensaje final del recibo",
        max_length=240,
        default="Gracias por tu visita :)",
        blank=True,
    )
    receipt_show_logo = models.BooleanField(
        "Mostrar logotipo",
        default=True,
    )
    receipt_show_qr = models.BooleanField(
        "Mostrar codigo QR",
        default=True,
    )
    updated_at = models.DateTimeField("Actualizado", auto_now=True)

    class Meta:
        verbose_name = "Configuracion del salon"
        verbose_name_plural = "Configuracion del salon"

    @classmethod
    def load(cls):
        settings_obj, _created = cls.objects.get_or_create(pk=1)
        return settings_obj

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)


class Zone(models.Model):
    class ZoneTypes(models.TextChoices):
        CABIN = "cabin", "Cabina"
        TABLE = "table", "Mesa"
        WASH = "wash", "Lavacabezas"
        MAKEUP = "makeup", "Maquillaje"
        OTHER = "other", "Otro"

    name = models.CharField("Nombre", max_length=150)
    zone_type = models.CharField(
        "Tipo",
        max_length=30,
        choices=ZoneTypes.choices,
        default=ZoneTypes.OTHER,
    )
    capacity = models.PositiveIntegerField("Capacidad", default=1)
    color = models.CharField("Color", max_length=20, default="#e291b3")
    is_active = models.BooleanField("Activa", default=True)
    notes = models.TextField("Notas", blank=True)
    created_at = models.DateTimeField("Creada", auto_now_add=True)
    updated_at = models.DateTimeField("Actualizada", auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Zona"
        verbose_name_plural = "Zonas"

    def __str__(self):
        return self.name
