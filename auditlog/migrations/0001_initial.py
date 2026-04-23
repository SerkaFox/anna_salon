from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("section", models.CharField(db_index=True, max_length=40, verbose_name="Sección")),
                ("action", models.CharField(db_index=True, max_length=40, verbose_name="Acción")),
                ("target_model", models.CharField(blank=True, max_length=120, verbose_name="Modelo")),
                ("target_id", models.CharField(blank=True, max_length=80, verbose_name="ID objetivo")),
                ("target_repr", models.CharField(blank=True, max_length=255, verbose_name="Objetivo")),
                ("message", models.CharField(max_length=255, verbose_name="Mensaje")),
                ("metadata", models.JSONField(blank=True, default=dict, verbose_name="Metadata")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Fecha")),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_events",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Usuario",
                    ),
                ),
            ],
            options={
                "verbose_name": "Evento de auditoría",
                "verbose_name_plural": "Eventos de auditoría",
                "ordering": ["-created_at", "-id"],
            },
        ),
    ]
