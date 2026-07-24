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
