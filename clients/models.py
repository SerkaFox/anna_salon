from django.db import models


class Client(models.Model):
    first_name = models.CharField("Nombre", max_length=120)
    last_name = models.CharField("Apellidos", max_length=150, blank=True)
    phone = models.CharField("Teléfono", max_length=30, blank=True)
    email = models.EmailField("Email", blank=True)
    birth_date = models.DateField("Fecha de nacimiento", null=True, blank=True)
    notes = models.TextField("Notas", blank=True)
    is_active = models.BooleanField("Activo", default=True)
    created_at = models.DateTimeField("Creado", auto_now_add=True)
    updated_at = models.DateTimeField("Actualizado", auto_now=True)

    referred_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="referred_clients",
        verbose_name="Recomendado por",
    )
    referral_rewards_used = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["first_name", "last_name"]
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"

    def __str__(self):
        full_name = f"{self.first_name} {self.last_name}".strip()
        return full_name or self.phone or f"Cliente #{self.pk}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()