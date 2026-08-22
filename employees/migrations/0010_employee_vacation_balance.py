import employees.models
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("employees", "0009_employee_zones")]

    operations = [
        migrations.AddField(
            model_name="employee",
            name="vacation_days_allowance",
            field=models.PositiveSmallIntegerField(
                default=30, verbose_name="Dias de vacaciones asignados"
            ),
        ),
        migrations.AddField(
            model_name="employee",
            name="vacation_days_used",
            field=models.PositiveSmallIntegerField(
                default=0, verbose_name="Dias de vacaciones usados"
            ),
        ),
        migrations.AddField(
            model_name="employee",
            name="vacation_year",
            field=models.PositiveSmallIntegerField(
                default=employees.models.current_year,
                verbose_name="Ano de vacaciones",
            ),
        ),
    ]
