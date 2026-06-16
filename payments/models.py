from django.db import models


class Payment(models.Model):
    class Providers(models.TextChoices):
        REDSYS = "redsys", "Redsys"
        STRIPE = "stripe", "Stripe"

    class Methods(models.TextChoices):
        CARD = "card", "Tarjeta"
        BIZUM = "bizum", "Bizum"
        UNKNOWN = "unknown", "Desconocido"

    class Statuses(models.TextChoices):
        PENDING = "pending", "Pendiente"
        PAID = "paid", "Pagado"
        FAILED = "failed", "Fallido"
        CANCELLED = "cancelled", "Cancelado"
        REFUNDED = "refunded", "Devuelto"
        EXPIRED = "expired", "Expirado"

    booking = models.ForeignKey(
        "bookings.Booking",
        on_delete=models.PROTECT,
        related_name="online_payments",
        verbose_name="Reserva",
    )
    amount = models.DecimalField("Importe", max_digits=10, decimal_places=2)
    currency = models.CharField("Moneda", max_length=3, default="978")
    order_number = models.CharField("Pedido / referencia", max_length=80, unique=True)
    provider = models.CharField(
        "Proveedor",
        max_length=20,
        choices=Providers.choices,
        default=Providers.REDSYS,
    )
    method = models.CharField(
        "Método",
        max_length=20,
        choices=Methods.choices,
        default=Methods.UNKNOWN,
    )
    status = models.CharField(
        "Estado",
        max_length=20,
        choices=Statuses.choices,
        default=Statuses.PENDING,
    )
    redsys_response_code = models.CharField("Código Redsys", max_length=10, blank=True)
    redsys_authorisation_code = models.CharField("Autorización Redsys", max_length=20, blank=True)
    stripe_checkout_session_id = models.CharField("Stripe Checkout Session", max_length=255, blank=True, db_index=True)
    stripe_payment_intent_id = models.CharField("Stripe PaymentIntent", max_length=255, blank=True, db_index=True)
    stripe_customer_email = models.EmailField("Email cliente Stripe", blank=True)
    checkout_url = models.TextField("URL de checkout", blank=True)
    raw_request = models.JSONField("Petición Redsys", default=dict, blank=True)
    raw_response = models.JSONField("Respuesta Redsys", default=dict, blank=True)
    raw_event = models.JSONField("Evento proveedor", default=dict, blank=True)
    created_at = models.DateTimeField("Creado", auto_now_add=True)
    updated_at = models.DateTimeField("Actualizado", auto_now=True)
    paid_at = models.DateTimeField("Pagado el", null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Pago online"
        verbose_name_plural = "Pagos online"

    def __str__(self):
        return f"{self.order_number} · {self.amount} EUR · {self.get_status_display()}"
