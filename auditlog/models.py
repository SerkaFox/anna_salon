from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
        verbose_name="Usuario",
    )
    section = models.CharField("Sección", max_length=40, db_index=True)
    action = models.CharField("Acción", max_length=40, db_index=True)
    target_model = models.CharField("Modelo", max_length=120, blank=True)
    target_id = models.CharField("ID objetivo", max_length=80, blank=True)
    target_repr = models.CharField("Objetivo", max_length=255, blank=True)
    message = models.CharField("Mensaje", max_length=255)
    metadata = models.JSONField("Metadata", default=dict, blank=True)
    created_at = models.DateTimeField("Fecha", auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Evento de auditoría"
        verbose_name_plural = "Eventos de auditoría"

    def __str__(self):
        return f"{self.created_at:%d/%m/%Y %H:%M} · {self.section} · {self.message}"
