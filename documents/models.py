from decimal import Decimal

from django.db import models
from django.utils import timezone


class FiscalDocument(models.Model):
    class DocumentTypes(models.TextChoices):
        RECEIPT = "receipt", "Recibo"
        INVOICE = "invoice", "Factura"
        PROFORMA = "proforma", "Proforma"

    class Statuses(models.TextChoices):
        DRAFT = "draft", "Borrador"
        ISSUED = "issued", "Emitido"
        CANCELLED = "cancelled", "Cancelado"

    booking = models.ForeignKey(
        "bookings.Booking",
        on_delete=models.PROTECT,
        related_name="fiscal_documents",
        verbose_name="Reserva",
    )
    document_type = models.CharField(
        "Tipo",
        max_length=20,
        choices=DocumentTypes.choices,
        default=DocumentTypes.RECEIPT,
    )
    status = models.CharField(
        "Estado",
        max_length=20,
        choices=Statuses.choices,
        default=Statuses.ISSUED,
    )
    number = models.CharField("Número", max_length=40, unique=True, blank=True)
    issue_date = models.DateField("Fecha de emisión", default=timezone.localdate)
    tax_rate = models.DecimalField("IVA %", max_digits=5, decimal_places=2, default=Decimal("0.00"))
    subtotal_amount = models.DecimalField("Base imponible", max_digits=10, decimal_places=2, default=Decimal("0.00"))
    tax_amount = models.DecimalField("IVA", max_digits=10, decimal_places=2, default=Decimal("0.00"))
    total_amount = models.DecimalField("Total", max_digits=10, decimal_places=2, default=Decimal("0.00"))
    notes = models.TextField("Notas", blank=True)
    created_at = models.DateTimeField("Creado", auto_now_add=True)
    updated_at = models.DateTimeField("Actualizado", auto_now=True)

    class Meta:
        ordering = ["-issue_date", "-id"]
        verbose_name = "Documento fiscal"
        verbose_name_plural = "Documentos fiscales"
        constraints = [
            models.UniqueConstraint(
                fields=["booking", "document_type"],
                condition=models.Q(status__in=["draft", "issued"]),
                name="unique_active_document_per_booking_type",
            )
        ]

    def __str__(self):
        return f"{self.get_document_type_display()} {self.number or 'sin número'}"

    @property
    def client(self):
        return self.booking.client

    @property
    def service(self):
        return self.booking.service

    @property
    def payments_total(self):
        total = sum((payment.amount for payment in self.payments.all()), Decimal("0.00"))
        return total

    @property
    def balance_due(self):
        balance = (self.total_amount or Decimal("0.00")) - self.payments_total
        return max(balance, Decimal("0.00"))

    @property
    def is_paid(self):
        return self.balance_due <= Decimal("0.00")

    def refresh_amounts_from_booking(self):
        total = self.booking.client_price_snapshot or Decimal("0.00")
        tax_rate = self.tax_rate or Decimal("0.00")

        if tax_rate:
            divisor = Decimal("1.00") + (tax_rate / Decimal("100.00"))
            subtotal = total / divisor
            tax_amount = total - subtotal
        else:
            subtotal = total
            tax_amount = Decimal("0.00")

        self.total_amount = total.quantize(Decimal("0.01"))
        self.subtotal_amount = subtotal.quantize(Decimal("0.01"))
        self.tax_amount = tax_amount.quantize(Decimal("0.01"))

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = self.build_next_number()

        self.refresh_amounts_from_booking()
        super().save(*args, **kwargs)

    def build_next_number(self):
        prefix_map = {
            self.DocumentTypes.RECEIPT: "REC",
            self.DocumentTypes.INVOICE: "FAC",
            self.DocumentTypes.PROFORMA: "PRO",
        }
        prefix = prefix_map.get(self.document_type, "DOC")
        year = self.issue_date.year if self.issue_date else timezone.localdate().year
        base = f"{prefix}-{year}-"

        last_document = (
            FiscalDocument.objects.filter(number__startswith=base)
            .exclude(pk=self.pk)
            .order_by("-number")
            .first()
        )
        last_sequence = 0

        if last_document:
            try:
                last_sequence = int(last_document.number.rsplit("-", 1)[1])
            except (IndexError, ValueError):
                last_sequence = 0

        return f"{base}{last_sequence + 1:04d}"


class Payment(models.Model):
    class Methods(models.TextChoices):
        CASH = "cash", "Efectivo"
        CARD = "card", "Tarjeta"
        BIZUM = "bizum", "Bizum"
        TRANSFER = "transfer", "Transferencia"

    fiscal_document = models.ForeignKey(
        FiscalDocument,
        on_delete=models.CASCADE,
        related_name="payments",
        verbose_name="Documento",
    )
    booking = models.ForeignKey(
        "bookings.Booking",
        on_delete=models.PROTECT,
        related_name="payments",
        verbose_name="Reserva",
    )
    paid_at = models.DateTimeField("Fecha de pago", default=timezone.now)
    amount = models.DecimalField("Importe", max_digits=10, decimal_places=2)
    method = models.CharField(
        "Método",
        max_length=20,
        choices=Methods.choices,
        default=Methods.CARD,
    )
    reference = models.CharField("Referencia", max_length=140, blank=True)
    notes = models.TextField("Notas", blank=True)
    created_at = models.DateTimeField("Creado", auto_now_add=True)

    class Meta:
        ordering = ["-paid_at", "-id"]
        verbose_name = "Pago"
        verbose_name_plural = "Pagos"

    def __str__(self):
        return f"{self.get_method_display()} · {self.amount} € · {self.booking.client}"

    def save(self, *args, **kwargs):
        if self.fiscal_document_id and not self.booking_id:
            self.booking = self.fiscal_document.booking
        super().save(*args, **kwargs)
