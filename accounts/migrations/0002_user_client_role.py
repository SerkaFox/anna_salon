from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("owner", "Owner"),
                    ("admin", "Admin"),
                    ("employee", "Employee"),
                    ("client", "Client"),
                ],
                default="employee",
                max_length=20,
            ),
        ),
    ]
