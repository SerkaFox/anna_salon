from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import FiscalDocument, FiscalDocumentLine


def _refresh_document(document):
    document.save(
        update_fields=[
            "subtotal_amount",
            "tax_amount",
            "total_amount",
            "updated_at",
        ]
    )
    document.refresh_from_db()
    _sync_booking_snapshots(document)
    return document


def _sync_booking_snapshots(document):
    """Sync booking commission snapshots when document lines change.

    When staff adds extra charges (e.g. nail art, special top coat) to a
    document, the document total grows but the booking's price/commission
    snapshots were frozen at creation time.  This keeps them in sync so
    that employee earnings analytics reflect the actual billed amount.

    Only runs for standard (non-prepayment) documents that have a booking.
    """
    if document.purpose != FiscalDocument.Purposes.STANDARD:
        return
    if not document.booking_id:
        return
    booking = document.booking
    new_total = document.total_amount
    percent = booking.employee_percent_snapshot or Decimal("0.00")
    employee_amount = (new_total * percent / Decimal("100")).quantize(Decimal("0.01"))
    salon_amount = (new_total - employee_amount).quantize(Decimal("0.01"))
    booking.client_price_snapshot = new_total
    booking.employee_amount_snapshot = employee_amount
    booking.salon_amount_snapshot = salon_amount
    booking.save(update_fields=[
        "client_price_snapshot",
        "employee_amount_snapshot",
        "salon_amount_snapshot",
        "updated_at",
    ])


@transaction.atomic
def update_document_line_price(line_id, unit_amount):
    line = FiscalDocumentLine.objects.select_for_update().get(pk=line_id)
    document = FiscalDocument.objects.select_for_update().get(
        pk=line.fiscal_document_id
    )
    unit_amount = Decimal(unit_amount).quantize(Decimal("0.01"))
    other_total = sum(
        (
            item.total_amount
            for item in FiscalDocumentLine.objects.filter(
                fiscal_document=document
            ).exclude(pk=line.pk)
        ),
        Decimal("0.00"),
    )
    resulting_total = other_total + (line.quantity * unit_amount)
    if resulting_total < document.payments_total:
        raise ValidationError(
            "El nuevo total no puede ser inferior al importe ya cobrado. "
            "Registra primero una devolución."
        )
    line.unit_amount = unit_amount
    line.save(update_fields=["unit_amount"])
    return _refresh_document(document), line


@transaction.atomic
def delete_document_line(line_id):
    line = FiscalDocumentLine.objects.select_for_update().get(pk=line_id)
    document = FiscalDocument.objects.select_for_update().get(
        pk=line.fiscal_document_id
    )
    lines = list(
        FiscalDocumentLine.objects.select_for_update().filter(
            fiscal_document=document
        )
    )
    if len(lines) <= 1:
        raise ValidationError("El documento debe conservar al menos una línea.")
    resulting_total = sum(
        (item.total_amount for item in lines if item.pk != line.pk),
        Decimal("0.00"),
    )
    if resulting_total < document.payments_total:
        raise ValidationError(
            "No se puede eliminar la línea porque el total quedaría por debajo "
            "del importe ya cobrado. Registra primero una devolución."
        )
    line.delete()
    return _refresh_document(document)
