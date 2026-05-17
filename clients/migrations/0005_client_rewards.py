from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


def seed_default_rewards(apps, schema_editor):
    ClientRewardRule = apps.get_model("clients", "ClientRewardRule")
    defaults = [
        {
            "name": "Amigos BRIMOON",
            "reward_type": "referrals",
            "threshold": 5,
            "discount_percent": Decimal("20.00"),
            "icon": "groups",
            "color": "#6FD29C",
            "sort_order": 1,
        },
        {
            "name": "Cliente fiel",
            "reward_type": "visits",
            "threshold": 5,
            "discount_percent": Decimal("10.00"),
            "icon": "star",
            "color": "#F4C95D",
            "sort_order": 2,
        },
        {
            "name": "VIP Studio",
            "reward_type": "spent",
            "threshold": 250,
            "discount_percent": Decimal("15.00"),
            "icon": "workspace_premium",
            "color": "#C792EA",
            "sort_order": 3,
        },
    ]
    for item in defaults:
        ClientRewardRule.objects.update_or_create(
            reward_type=item["reward_type"],
            defaults=item,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0010_bookingphoto_is_visible_to_client"),
        ("clients", "0004_client_avatar"),
    ]

    operations = [
        migrations.CreateModel(
            name="ClientRewardRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, verbose_name="Nombre")),
                ("reward_type", models.CharField(choices=[("referrals", "Amigos"), ("visits", "Visitas"), ("spent", "VIP")], max_length=20, unique=True, verbose_name="Tipo")),
                ("threshold", models.PositiveIntegerField(default=5, verbose_name="Objetivo")),
                ("discount_percent", models.DecimalField(decimal_places=2, default=20, max_digits=5, verbose_name="Descuento %")),
                ("icon", models.CharField(default="card_giftcard", max_length=40, verbose_name="Icono")),
                ("color", models.CharField(default="#6FD29C", max_length=20, verbose_name="Color")),
                ("is_active", models.BooleanField(default=True, verbose_name="Activa")),
                ("sort_order", models.PositiveIntegerField(default=0, verbose_name="Orden")),
            ],
            options={
                "verbose_name": "Premio de cliente",
                "verbose_name_plural": "Premios de clientes",
                "ordering": ["sort_order", "name"],
            },
        ),
        migrations.CreateModel(
            name="ClientRewardRedemption",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("discount_amount", models.DecimalField(decimal_places=2, default=0, max_digits=10, verbose_name="Descuento aplicado")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Creado")),
                ("booking", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reward_redemptions", to="bookings.booking", verbose_name="Reserva")),
                ("client", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reward_redemptions", to="clients.client", verbose_name="Cliente")),
                ("reward_rule", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="redemptions", to="clients.clientrewardrule", verbose_name="Premio")),
            ],
            options={
                "verbose_name": "Premio usado",
                "verbose_name_plural": "Premios usados",
                "ordering": ["-created_at"],
            },
        ),
        migrations.RunPython(seed_default_rewards, migrations.RunPython.noop),
    ]
