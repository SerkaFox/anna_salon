"""Cuanto se ha cobrado realmente por cada reserva.

Lo que gana una empleada no sale del precio con el que nacio la reserva, sino
del dinero que acaba entrando: por el camino hay suplementos, cambios de
servicio y correcciones, y el recibo es el que recoge el importe final.

Dos fuentes de dinero y una trampa: el recibo se emite por el total del
servicio con el prepago incluido (el cobro rapido usa `document.balance_due`,
que no descuenta lo ya pagado por Stripe). Sumar recibos y pagos online
contaria el prepago dos veces, asi que manda el recibo y lo cobrado online
solo cuenta cuando no hay ninguno.
"""

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Case, DecimalField, F, Sum, When


def collected_by_booking(booking_ids):
    """{booking_id: importe cobrado} para las reservas indicadas."""
    from documents.models import Payment as ManualPayment
    from payments.models import Payment as OnlinePayment

    booking_ids = list(booking_ids)
    collected = defaultdict(Decimal)
    if not booking_ids:
        return collected

    # Las devoluciones restan: entry_type=refund guarda el importe en positivo.
    for row in (
        ManualPayment.objects.filter(booking_id__in=booking_ids)
        .values("booking_id")
        .annotate(
            total=Sum(
                Case(
                    When(
                        entry_type=ManualPayment.EntryTypes.REFUND,
                        then=-F("amount"),
                    ),
                    default=F("amount"),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                )
            )
        )
    ):
        collected[row["booking_id"]] = row["total"] or Decimal("0.00")

    online = defaultdict(Decimal)
    for payment in OnlinePayment.objects.filter(
        booking_id__in=booking_ids,
        status__in=[
            OnlinePayment.Statuses.PAID,
            OnlinePayment.Statuses.PARTIALLY_REFUNDED,
        ],
    ):
        online[payment.booking_id] += payment.amount - (
            payment.amount_refunded or Decimal("0.00")
        )

    for booking_id, amount in online.items():
        if collected.get(booking_id, Decimal("0.00")) <= Decimal("0.00"):
            collected[booking_id] = amount

    return collected


def split_booking_amount(booking, collected):
    """Reparte lo cobrado entre empleada y salon con el % de la reserva."""
    percent = booking.employee_percent_snapshot or Decimal("0.00")
    employee_amount = (collected * percent / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return employee_amount, collected - employee_amount


def expected_amount(booking):
    """Lo que deberia cobrarse por la reserva, para detectar lo no cobrado."""
    return (
        booking.client_price_snapshot
        or booking.price_snapshot
        or Decimal("0.00")
    )
