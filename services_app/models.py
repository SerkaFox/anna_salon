from decimal import Decimal
from django.db import models


class Service(models.Model):
    class Categories(models.TextChoices):
        BROWS = "brows", "Cejas"
        LASHES = "lashes", "Pestañas"
        MANICURE = "manicure", "Manicura"
        PEDICURE = "pedicure", "Pedicura"
        DEPILATION = "depilation", "Depilación"
        FACIAL = "facial", "Facial"
        OTHER = "other", "Otro"

    name = models.CharField("Nombre", max_length=150)
    category = models.CharField(
        "Categoría",
        max_length=20,
        choices=Categories.choices,
        default=Categories.OTHER,
    )
    description = models.TextField("Descripción", blank=True)
    duration_minutes = models.PositiveIntegerField("Duración (minutos)", default=60)
    price = models.DecimalField("Precio", max_digits=10, decimal_places=2, default=Decimal("0.00"))
    color = models.CharField("Color", max_length=20, default="#6FD29C")
    requires_zone = models.BooleanField("Requiere zona/recurso", default=False)
    allowed_zones = models.ManyToManyField(
        "salon.Zone",
        blank=True,
        related_name="services",
        verbose_name="Zonas permitidas",
    )
    is_active = models.BooleanField("Activo", default=True)
    created_at = models.DateTimeField("Creado", auto_now_add=True)
    updated_at = models.DateTimeField("Actualizado", auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Servicio"
        verbose_name_plural = "Servicios"

    def __str__(self):
        return self.name
