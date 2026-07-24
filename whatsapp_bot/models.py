import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class WhatsAppConnection(models.Model):
    class Statuses(models.TextChoices):
        DISCONNECTED = "disconnected", "Disconnected"
        QR_PENDING = "qr_pending", "QR pending"
        CONNECTED = "connected", "Connected"
        ERROR = "error", "Error"

    name = models.CharField(max_length=120, unique=True, default="main")
    status = models.CharField(max_length=20, choices=Statuses.choices, default=Statuses.DISCONNECTED)
    phone = models.CharField(max_length=40, blank=True)
    bridge_session_id = models.CharField(max_length=120, blank=True)
    last_error = models.TextField(blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.status})"


class WhatsAppLoginLink(models.Model):
    token = models.CharField(max_length=80, unique=True, db_index=True)
    connection = models.ForeignKey(WhatsAppConnection, on_delete=models.CASCADE, related_name="login_links")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=180, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @classmethod
    def create_for_connection(cls, connection, *, ttl_minutes=15, note=""):
        return cls.objects.create(
            token=secrets.token_urlsafe(32),
            connection=connection,
            expires_at=timezone.now() + timedelta(minutes=ttl_minutes),
            note=note,
        )

    @property
    def is_valid(self):
        return self.used_at is None and self.expires_at > timezone.now()

    def mark_used(self):
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])

    def __str__(self):
        return f"{self.connection.name} login link"


class WhatsAppMessage(models.Model):
    class Kinds(models.TextChoices):
        WAITLIST_JOINED = 'waitlist_joined', 'Waitlist joined'
        WAITLIST_SLOT_AVAILABLE = 'waitlist_slot_available', 'Waitlist slot available'
        BOOKING_CONFIRMATION = "booking_confirmation", "Booking confirmation"
        BOOKING_CANCELLED = "booking_cancelled", "Booking cancelled"
        BOOKING_RESCHEDULED = "booking_rescheduled", "Booking rescheduled"
        REMINDER_24H = "reminder_24h", "Reminder 24h"
        REMINDER_2H = "reminder_2h", "Reminder 2h"
        WELCOME_CREDENTIALS = "welcome_credentials", "Welcome / login credentials"
        PAYMENT_RECEIPT = "payment_receipt", "Payment receipt"
        BIRTHDAY_GREETING = "birthday_greeting", "Birthday greeting"
        MANUAL = "manual", "Manual"

    class Statuses(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    connection = models.ForeignKey(WhatsAppConnection, on_delete=models.PROTECT, related_name="messages")
    booking = models.ForeignKey("bookings.Booking", on_delete=models.CASCADE, null=True, blank=True, related_name="whatsapp_messages")
    waitlist_entry = models.ForeignKey(
        "bookings.BookingWaitlistEntry",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="whatsapp_messages",
    )
    client = models.ForeignKey("clients.Client", on_delete=models.SET_NULL, null=True, blank=True, related_name="whatsapp_messages")
    kind = models.CharField(max_length=40, choices=Kinds.choices)
    to_phone = models.CharField(max_length=40)
    body = models.TextField()
    status = models.CharField(max_length=20, choices=Statuses.choices, default=Statuses.QUEUED, db_index=True)
    scheduled_for = models.DateTimeField(default=timezone.now, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    provider_message_id = models.CharField(max_length=180, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["booking", "kind"], name="unique_whatsapp_booking_message_kind"),
            models.UniqueConstraint(
                fields=["waitlist_entry", "kind"],
                name="unique_whatsapp_waitlist_message_kind",
            ),
        ]

    def __str__(self):
        return f"{self.kind} to {self.to_phone} ({self.status})"


# Default bodies are defined here so services.py can import them and fall back to them.
TEMPLATE_DEFAULTS = {
    WhatsAppMessage.Kinds.BOOKING_CONFIRMATION: (
        "Hola {client_name} 👋 Tu cita en {salon_name} está confirmada:\n"
        "📅 {date} a las {time}\n"
        "💅 {service_name}\n\n"
        "Accede a tu área privada para pagar la señal:\n"
        "🔗 {booking_url}"
    ),
    WhatsAppMessage.Kinds.BOOKING_CANCELLED: (
        "Hola {client_name}. Tu cita en {salon_name} del {date} "
        "a las {time} ({service_name}) ha sido cancelada.\n"
        "Si quieres volver a reservar: {portal_url}"
    ),
    WhatsAppMessage.Kinds.BOOKING_RESCHEDULED: (
        "Hola {client_name}. Tu cita en {salon_name} ha sido reagendada:\n"
        "📅 {date} a las {time}\n"
        "💅 {service_name}\n\n"
        "Ver detalles: {portal_url}"
    ),
    WhatsAppMessage.Kinds.REMINDER_24H: (
        "Hola {client_name} 👋 Te recordamos tu cita en {salon_name} mañana "
        "{date} a las {time} para {service_name}."
    ),
    WhatsAppMessage.Kinds.REMINDER_2H: (
        "Hola {client_name} 👋 Te esperamos en {salon_name} en 2 horas, "
        "a las {time}, para {service_name}."
    ),
    WhatsAppMessage.Kinds.WELCOME_CREDENTIALS: (
        "Hola {client_name} 👋 Bienvenida/o a {salon_name}.\n\n"
        "Tus datos de acceso al área privada:\n"
        "🔑 Usuario: {username}\n"
        "🔒 Contraseña: {password}\n\n"
        "Accede aquí:\n🔗 {portal_url}\n\n"
        "Guarda este mensaje para no perder tus datos."
    ),
    WhatsAppMessage.Kinds.BIRTHDAY_GREETING: (
        "Hola {client_name} 🎂 ¡Feliz cumpleaños de parte de todo el equipo de {salon_name}!\n\n"
        "Como regalo te esperamos con {offer} 🎁\n\n"
        "¡Que tengas un día maravilloso! 💅"
    ),
    WhatsAppMessage.Kinds.WAITLIST_JOINED: (
        'Nueva solicitud para la lista de espera de {salon_name}.\n\n'
        'Cliente: {client_name}\nServicio: {service_name}\nFecha: {date}\n'
        'Horario: {time_range}\nTelefono: {phone}\nEmail: {email}'
    ),
    WhatsAppMessage.Kinds.WAITLIST_SLOT_AVAILABLE: (
        'Hola {client_name}. Se ha liberado un hueco en {salon_name}:\n'
        '{date} a las {time}, {service_name} con {employee_name}.\n\n'
        'Reserva cuanto antes desde {booking_url} o contacta con el salon.'
    ),
}

TEMPLATE_NAMES = {
    WhatsAppMessage.Kinds.BOOKING_CONFIRMATION: "Confirmación de cita",
    WhatsAppMessage.Kinds.BOOKING_CANCELLED: "Cancelación de cita",
    WhatsAppMessage.Kinds.BOOKING_RESCHEDULED: "Reagendamiento de cita",
    WhatsAppMessage.Kinds.REMINDER_24H: "Recordatorio 24h antes",
    WhatsAppMessage.Kinds.REMINDER_2H: "Recordatorio 2h antes",
    WhatsAppMessage.Kinds.WELCOME_CREDENTIALS: "Bienvenida con credenciales",
    WhatsAppMessage.Kinds.BIRTHDAY_GREETING: "Felicitación de cumpleaños",
    WhatsAppMessage.Kinds.WAITLIST_JOINED: 'Nueva persona en lista de espera',
    WhatsAppMessage.Kinds.WAITLIST_SLOT_AVAILABLE: 'Hueco libre para lista de espera',
}

TEMPLATE_VARIABLES = {
    WhatsAppMessage.Kinds.BOOKING_CONFIRMATION: "{client_name} {salon_name} {date} {time} {service_name} {booking_url} {portal_url}",
    WhatsAppMessage.Kinds.BOOKING_CANCELLED: "{client_name} {salon_name} {date} {time} {service_name} {portal_url}",
    WhatsAppMessage.Kinds.BOOKING_RESCHEDULED: "{client_name} {salon_name} {date} {time} {service_name} {portal_url}",
    WhatsAppMessage.Kinds.REMINDER_24H: "{client_name} {salon_name} {date} {time} {service_name} {attend_url} {decline_url}",
    WhatsAppMessage.Kinds.REMINDER_2H: "{client_name} {salon_name} {time} {service_name}",
    WhatsAppMessage.Kinds.WELCOME_CREDENTIALS: "{client_name} {salon_name} {username} {password} {portal_url}",
    WhatsAppMessage.Kinds.BIRTHDAY_GREETING: "{client_name} {salon_name} {offer}",
    WhatsAppMessage.Kinds.WAITLIST_JOINED: '{client_name} {salon_name} {service_name} {date} {time_range} {phone} {email}',
    WhatsAppMessage.Kinds.WAITLIST_SLOT_AVAILABLE: '{client_name} {salon_name} {service_name} {employee_name} {date} {time} {booking_url}',
}


class WhatsAppTemplate(models.Model):
    kind = models.CharField(
        max_length=40, choices=WhatsAppMessage.Kinds.choices, unique=True
    )
    name = models.CharField(max_length=120)
    body = models.TextField()
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["kind"]

    def __str__(self):
        status = "✓" if self.enabled else "✗"
        return f"[{status}] {self.name}"

    @classmethod
    def get_body(cls, kind):
        """Return enabled template body from DB or hardcoded default."""
        try:
            tmpl = cls.objects.get(kind=kind)
            if not tmpl.enabled:
                return None
            return tmpl.body
        except cls.DoesNotExist:
            return TEMPLATE_DEFAULTS.get(kind)

    @classmethod
    def is_enabled(cls, kind):
        try:
            return cls.objects.get(kind=kind).enabled
        except cls.DoesNotExist:
            return True  # enabled by default if no DB record

    @classmethod
    def ensure_defaults(cls):
        """Create DB rows for any kinds not yet in the DB."""
        for kind, body in TEMPLATE_DEFAULTS.items():
            cls.objects.get_or_create(
                kind=kind,
                defaults={"name": TEMPLATE_NAMES.get(kind, kind), "body": body},
            )
