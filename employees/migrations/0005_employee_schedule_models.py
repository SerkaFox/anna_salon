from datetime import time

from django.db import migrations, models


def create_default_weekly_shifts(apps, schema_editor):
    Employee = apps.get_model("employees", "Employee")
    EmployeeWeeklyShift = apps.get_model("employees", "EmployeeWeeklyShift")

    for employee in Employee.objects.all():
        for weekday in range(7):
            EmployeeWeeklyShift.objects.get_or_create(
                employee=employee,
                weekday=weekday,
                defaults={
                    "is_day_off": weekday == 6,
                    "start_time": None if weekday == 6 else time(hour=9, minute=0),
                    "end_time": None if weekday == 6 else time(hour=20, minute=0),
                },
            )


class Migration(migrations.Migration):
    dependencies = [
        ("employees", "0004_employee_commission_percent"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmployeeWeeklyShift",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("weekday", models.PositiveSmallIntegerField(choices=[(0, "Lunes"), (1, "Martes"), (2, "Miércoles"), (3, "Jueves"), (4, "Viernes"), (5, "Sábado"), (6, "Domingo")], verbose_name="Día de la semana")),
                ("is_day_off", models.BooleanField(default=False, verbose_name="Día libre")),
                ("start_time", models.TimeField(blank=True, null=True, verbose_name="Inicio")),
                ("end_time", models.TimeField(blank=True, null=True, verbose_name="Fin")),
                ("break_start", models.TimeField(blank=True, null=True, verbose_name="Inicio pausa")),
                ("break_end", models.TimeField(blank=True, null=True, verbose_name="Fin pausa")),
                ("note", models.CharField(blank=True, max_length=140, verbose_name="Nota")),
                ("employee", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="weekly_shifts", to="employees.employee", verbose_name="Empleado")),
            ],
            options={
                "verbose_name": "Turno semanal",
                "verbose_name_plural": "Turnos semanales",
                "ordering": ["employee", "weekday"],
                "unique_together": {("employee", "weekday")},
            },
        ),
        migrations.CreateModel(
            name="EmployeeScheduleOverride",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField(verbose_name="Fecha")),
                ("is_day_off", models.BooleanField(default=False, verbose_name="Día libre")),
                ("start_time", models.TimeField(blank=True, null=True, verbose_name="Inicio")),
                ("end_time", models.TimeField(blank=True, null=True, verbose_name="Fin")),
                ("break_start", models.TimeField(blank=True, null=True, verbose_name="Inicio pausa")),
                ("break_end", models.TimeField(blank=True, null=True, verbose_name="Fin pausa")),
                ("label", models.CharField(blank=True, max_length=140, verbose_name="Motivo")),
                ("employee", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="schedule_overrides", to="employees.employee", verbose_name="Empleado")),
            ],
            options={
                "verbose_name": "Excepción de horario",
                "verbose_name_plural": "Excepciones de horario",
                "ordering": ["employee", "date"],
                "unique_together": {("employee", "date")},
            },
        ),
        migrations.RunPython(create_default_weekly_shifts, migrations.RunPython.noop),
    ]
