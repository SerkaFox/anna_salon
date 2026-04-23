from datetime import date

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class Weekday(models.IntegerChoices):
    MONDAY = 0, _("Lunes")
    TUESDAY = 1, _("Martes")
    WEDNESDAY = 2, _("Miércoles")
    THURSDAY = 3, _("Jueves")
    FRIDAY = 4, _("Viernes")
    SATURDAY = 5, _("Sábado")
    SUNDAY = 6, _("Domingo")


class Employee(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employee_profile",
        verbose_name="Usuario"
    )
    first_name = models.CharField("Nombre", max_length=120)
    last_name = models.CharField("Apellidos", max_length=150, blank=True)
    phone = models.CharField("Teléfono", max_length=30, blank=True)
    email = models.EmailField("Email", blank=True)
    services = models.ManyToManyField(
        "services_app.Service",
        blank=True,
        related_name="employees",
        verbose_name="Servicios que realiza",
    )
    calendar_color = models.CharField("Color calendario", max_length=20, default="#c75c8b")
    commission_percent = models.DecimalField(
        "Porcentaje del empleado",
        max_digits=5,
        decimal_places=2,
        default=40
    )
    is_active = models.BooleanField("Activo", default=True)
    notes = models.TextField("Notas", blank=True)
    created_at = models.DateTimeField("Creado", auto_now_add=True)
    updated_at = models.DateTimeField("Actualizado", auto_now=True)

    class Meta:
        ordering = ["first_name", "last_name"]
        verbose_name = "Empleado"
        verbose_name_plural = "Empleados"

    def __str__(self):
        return self.full_name

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def get_shift_for_date(self, target_date: date):
        override = self.schedule_overrides.filter(date=target_date).first()
        if override:
            return override
        return self.weekly_shifts.filter(weekday=target_date.weekday()).first()


class EmployeeWeeklyShift(models.Model):
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="weekly_shifts",
        verbose_name="Empleado",
    )
    weekday = models.PositiveSmallIntegerField("Día de la semana", choices=Weekday.choices)
    is_day_off = models.BooleanField("Día libre", default=False)
    start_time = models.TimeField("Inicio", null=True, blank=True)
    end_time = models.TimeField("Fin", null=True, blank=True)
    break_start = models.TimeField("Inicio pausa", null=True, blank=True)
    break_end = models.TimeField("Fin pausa", null=True, blank=True)
    break_label = models.CharField("Motivo pausa", max_length=140, blank=True)
    note = models.CharField("Nota", max_length=140, blank=True)

    class Meta:
        ordering = ["employee", "weekday"]
        unique_together = ["employee", "weekday"]
        verbose_name = "Turno semanal"
        verbose_name_plural = "Turnos semanales"

    def __str__(self):
        return f"{self.employee} · {self.get_weekday_display()}"


class EmployeeScheduleOverride(models.Model):
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="schedule_overrides",
        verbose_name="Empleado",
    )
    date = models.DateField("Fecha")
    is_day_off = models.BooleanField("Día libre", default=False)
    start_time = models.TimeField("Inicio", null=True, blank=True)
    end_time = models.TimeField("Fin", null=True, blank=True)
    break_start = models.TimeField("Inicio pausa", null=True, blank=True)
    break_end = models.TimeField("Fin pausa", null=True, blank=True)
    break_label = models.CharField("Motivo pausa", max_length=140, blank=True)
    label = models.CharField("Motivo", max_length=140, blank=True)

    class Meta:
        ordering = ["employee", "date"]
        unique_together = ["employee", "date"]
        verbose_name = "Excepción de horario"
        verbose_name_plural = "Excepciones de horario"

    def __str__(self):
        return f"{self.employee} · {self.date:%d/%m/%Y}"


class EmployeeTimeBlock(models.Model):
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="time_blocks",
        verbose_name="Empleado",
    )
    date = models.DateField("Fecha")
    start_time = models.TimeField("Inicio")
    end_time = models.TimeField("Fin")
    label = models.CharField("Motivo", max_length=140, blank=True)
    color = models.CharField("Color", max_length=20, default="#111111")

    class Meta:
        ordering = ["employee", "date", "start_time", "end_time", "pk"]
        verbose_name = "Bloqueo horario"
        verbose_name_plural = "Bloqueos horarios"

    def __str__(self):
        return f"{self.employee} · {self.date:%d/%m/%Y} · {self.start_time:%H:%M}-{self.end_time:%H:%M}"
