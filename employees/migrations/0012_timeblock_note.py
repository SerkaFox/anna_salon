from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('employees', '0011_employeescheduleoverride_is_vacation'),
    ]

    operations = [
        migrations.AddField(
            model_name='employeetimeblock',
            name='note',
            field=models.CharField(blank=True, max_length=300, verbose_name='Nota'),
        ),
        migrations.AddField(
            model_name='employeerecurringtimeblock',
            name='note',
            field=models.CharField(blank=True, max_length=300, verbose_name='Nota'),
        ),
    ]
