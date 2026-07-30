from django.conf import settings
from django.db import models


class Client(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_profile",
        verbose_name="Usuario",
    )
    avatar = models.ImageField("Avatar", upload_to="client_avatars/%Y/%m/", null=True, blank=True)
    first_name = models.CharField("Nombre", max_length=120)
    last_name = models.CharField("Apellidos", max_length=150, blank=True)
    phone = models.CharField("Teléfono", max_length=30, blank=True)
    alternate_phone = models.CharField("Teléfono alternativo", max_length=30, blank=True)
    email = models.EmailField("Email", blank=True)
    address = models.CharField("Dirección", max_length=255, blank=True)
    postcode = models.CharField("Código postal", max_length=20, blank=True)
    fiscal_id = models.CharField("NIE/NIF/CIF", max_length=40, blank=True)
    fiscal_address = models.CharField("Dirección fiscal", max_length=255, blank=True)
    fiscal_city = models.CharField("Ciudad", max_length=120, blank=True)
    fiscal_postcode = models.CharField("Código postal", max_length=20, blank=True)
    birth_date = models.DateField("Fecha de nacimiento", null=True, blank=True)
    gender = models.CharField("Género", max_length=20, blank=True)
    notes = models.TextField("Notas", blank=True)
    how_we_met = models.CharField("Cómo nos conoció", max_length=80, blank=True)
    external_source = models.CharField("Origen externo", max_length=40, blank=True)
    external_id = models.CharField("ID externo", max_length=120, blank=True)
    booking_count = models.PositiveIntegerField("Reservas históricas", default=0)
    last_appointment_at = models.DateField("Última cita", null=True, blank=True)
    acquisition_date = models.DateField("Fecha de captación", null=True, blank=True)
    acquired_at = models.DateField("Captado el", null=True, blank=True)
    average_expense_amount_cents = models.PositiveIntegerField(
        "Gasto medio (céntimos)",
        default=0,
    )
    appointments_frequency = models.CharField(
        "Frecuencia de citas",
        max_length=80,
        blank=True,
    )
    vat_number = models.CharField("Número IVA", max_length=60, blank=True)
    marketing_communications_accepted_at = models.DateTimeField(
        "Marketing aceptado el",
        null=True,
        blank=True,
    )
    marketing_communications_accepted_by_venue = models.BooleanField(
        "Marketing aceptado en salón",
        default=False,
    )
    marketing_communications_updated_at = models.DateTimeField(
        "Marketing actualizado el",
        null=True,
        blank=True,
    )
    marketing_communications_updated_by_venue = models.BooleanField(
        "Marketing actualizado en salón",
        default=False,
    )
    should_receive_marketing_email_notifications = models.BooleanField(
        "Marketing por email",
        default=False,
    )
    should_receive_marketing_sms_notifications = models.BooleanField(
        "Marketing por SMS",
        default=False,
    )
    consent_notification_services = models.BooleanField(
        "Consentimiento avisos de servicio",
        default=False,
    )
    consent_notification_marketing = models.BooleanField(
        "Consentimiento marketing",
        default=False,
    )
    is_blacklisted = models.BooleanField("Lista negra", default=False)
    is_active = models.BooleanField("Activo", default=True)
    notify_whatsapp = models.BooleanField("Notif. WhatsApp", default=True)
    notify_email = models.BooleanField("Notif. Email", default=True)
    created_at = models.DateTimeField("Creado", auto_now_add=True)
    updated_at = models.DateTimeField("Actualizado", auto_now=True)

    referred_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="referred_clients",
        verbose_name="Recomendado por",
    )
    referral_rewards_used = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["first_name", "last_name"]
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"

    def __str__(self):
        full_name = f"{self.first_name} {self.last_name}".strip()
        return full_name or self.phone or f"Cliente #{self.pk}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()


class ClientRewardRule(models.Model):
    class RewardTypes(models.TextChoices):
        REFERRALS = "referrals", "Amigos"
        VISITS = "visits", "Visitas"
        SPENT = "spent", "VIP"

    name = models.CharField("Nombre", max_length=120)
    reward_type = models.CharField("Tipo", max_length=20, choices=RewardTypes.choices, unique=True)
    threshold = models.PositiveIntegerField("Objetivo", default=5)
    discount_percent = models.DecimalField("Descuento %", max_digits=5, decimal_places=2, default=20)
    icon = models.CharField("Icono", max_length=40, default="card_giftcard")
    color = models.CharField("Color", max_length=20, default="#6FD29C")
    is_active = models.BooleanField("Activa", default=True)
    sort_order = models.PositiveIntegerField("Orden", default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "Premio de cliente"
        verbose_name_plural = "Premios de clientes"

    def __str__(self):
        return self.name


class ClientRewardRedemption(models.Model):
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="reward_redemptions",
        verbose_name="Cliente",
    )
    reward_rule = models.ForeignKey(
        ClientRewardRule,
        on_delete=models.PROTECT,
        related_name="redemptions",
        verbose_name="Premio",
    )
    booking = models.ForeignKey(
        "bookings.Booking",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reward_redemptions",
        verbose_name="Reserva",
    )
    discount_amount = models.DecimalField("Descuento aplicado", max_digits=10, decimal_places=2, default=0)
    created_at = models.DateTimeField("Creado", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Premio usado"
        verbose_name_plural = "Premios usados"

    def __str__(self):
        return f"{self.client} · {self.reward_rule}"
