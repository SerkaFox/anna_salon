from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("clients", "0002_client_referral_rewards_used_client_referred_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="client",
            name="user",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="client_profile",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Usuario",
            ),
        ),
    ]
