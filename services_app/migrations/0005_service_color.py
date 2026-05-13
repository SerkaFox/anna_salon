from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("services_app", "0004_delete_servicecategory"),
    ]

    operations = [
        migrations.AddField(
            model_name="service",
            name="color",
            field=models.CharField(default="#6FD29C", max_length=20, verbose_name="Color"),
        ),
    ]
