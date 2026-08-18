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
    return document


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
