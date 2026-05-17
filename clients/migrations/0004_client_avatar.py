from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0003_client_user"),
    ]

    operations = [
        migrations.AddField(
            model_name="client",
            name="avatar",
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to="client_avatars/%Y/%m/",
                verbose_name="Avatar",
            ),
        ),
    ]
