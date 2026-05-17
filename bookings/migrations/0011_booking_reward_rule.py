from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0005_client_rewards"),
        ("bookings", "0010_bookingphoto_is_visible_to_client"),
    ]

    operations = [
        migrations.AddField(
            model_name="booking",
            name="reward_rule",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="bookings",
                to="clients.clientrewardrule",
                verbose_name="Premio aplicado",
            ),
        ),
    ]
