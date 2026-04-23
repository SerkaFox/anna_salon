from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0006_employeetimeblock"),
    ]

    operations = [
        migrations.AddField(
            model_name="employeescheduleoverride",
            name="break_label",
            field=models.CharField(blank=True, max_length=140, verbose_name="Motivo pausa"),
        ),
        migrations.AddField(
            model_name="employeeweeklyshift",
            name="break_label",
            field=models.CharField(blank=True, max_length=140, verbose_name="Motivo pausa"),
        ),
    ]
