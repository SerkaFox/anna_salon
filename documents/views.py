import csv
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import admin_required
from auditlog.services import log_event
from bookings.models import Booking

from .forms import PaymentForm
from .models import CashClosure, FiscalDocument, Payment


def _is_cashbox_closed(target_date):
    return CashClosure.objects.filter(closure_date=target_date).exists()


def _parse_cashbox_date(request):
    date_value = request.GET.get("date", "").strip()
    if date_value:
        try:
            return timezone.datetime.strptime(date_value, "%Y-%m-%d").date()
        except ValueError:
            return timezone.localdate()
    return timezone.localdate()


def _get_or_create_payment_document(booking):
    active_documents = [
        document
        for document in booking.fiscal_documents.all()
        if document.status in {FiscalDocument.Statuses.DRAFT, FiscalDocument.Statuses.ISSUED}
    ]

    for document_type in (FiscalDocument.DocumentTypes.INVOICE, FiscalDocument.DocumentTypes.RECEIPT):
        for document in active_documents:
            if document.document_type == document_type:
                return document, False

    document = FiscalDocument.objects.create(
        booking=booking,
        document_type=FiscalDocument.DocumentTypes.RECEIPT,
        status=FiscalDocument.Statuses.ISSUED,
        issue_date=timezone.localdate(),
    )
    return document, True


def _filtered_documents(request):
    query = request.GET.get("q", "").strip()
    document_type = request.GET.get("type", "").strip()
    status = request.GET.get("status", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()

    documents = FiscalDocument.objects.select_related(
        "booking",
        "booking__client",
        "booking__employee",
        "booking__service",
    ).prefetch_related("payments")

    if query:
        documents = documents.filter(
            Q(number__icontains=query)
            | Q(booking__client__first_name__icontains=query)
            | Q(booking__client__last_name__icontains=query)
            | Q(booking__client__phone__icontains=query)
            | Q(booking__service__name__icontains=query)
        )

    if document_type:
        documents = documents.filter(document_type=document_type)

    if status:
        documents = documents.filter(status=status)

    if date_from:
        documents = documents.filter(issue_date__gte=date_from)

    if date_to:
        documents = documents.filter(issue_date__lte=date_to)

    return documents, {
        "query": query,
        "document_type": document_type,
        "status": status,
        "date_from": date_from,
        "date_to": date_to,
    }


def _pending_documents(documents):
    return [document for document in documents if document.balance_due > Decimal("0.00")]


@login_required
@admin_required
def document_list(request):
    documents, filters = _filtered_documents(request)
    pending_documents = _pending_documents(documents)
    totals = documents.aggregate(
        subtotal=Sum("subtotal_amount"),
        tax=Sum("tax_amount"),
        total=Sum("total_amount"),
    )

    return render(
        request,
        "documents/document_list.html",
        {
            "active_section": "documents",
            "documents": documents,
            "document_count": documents.count(),
            "pending_count": len(pending_documents),
            "pending_total": sum((document.balance_due for document in pending_documents), Decimal("0.00")),
            "totals": totals,
            "document_type_choices": FiscalDocument.DocumentTypes.choices,
            "status_choices": FiscalDocument.Statuses.choices,
            "payment_method_choices": Payment.Methods.choices,
            **filters,
        },
    )


@login_required
@admin_required
def unpaid_documents(request):
    documents, filters = _filtered_documents(request)
    pending_documents = _pending_documents(documents)
    pending_total = sum((document.balance_due for document in pending_documents), Decimal("0.00"))

    return render(
        request,
        "documents/unpaid_documents.html",
        {
            "active_section": "documents",
            "documents": pending_documents,
            "document_count": len(pending_documents),
            "pending_total": pending_total,
            "document_type_choices": FiscalDocument.DocumentTypes.choices,
            "payment_method_choices": Payment.Methods.choices,
            **filters,
        },
    )


@login_required
@require_POST
@admin_required
def document_create_from_booking(request, booking_pk, document_type):
    if document_type not in FiscalDocument.DocumentTypes.values:
        messages.error(request, "Tipo de documento no válido.")
        return redirect("bookings:list")

    booking = get_object_or_404(
        Booking.objects.select_related("client", "employee", "service"),
        pk=booking_pk,
    )
    document, created = FiscalDocument.objects.get_or_create(
        booking=booking,
        document_type=document_type,
        status__in=[FiscalDocument.Statuses.DRAFT, FiscalDocument.Statuses.ISSUED],
        defaults={
            "status": FiscalDocument.Statuses.ISSUED,
            "issue_date": timezone.localdate(),
        },
    )

    if not created:
        messages.info(request, f"Ya existe: {document}.")
    else:
        log_event(
            actor=request.user,
            section="document",
            action="create",
            instance=document,
            message=f"Documento creado: {document.number}.",
        )
        messages.success(request, f"Documento creado: {document}.")

    return redirect("documents:detail", pk=document.pk)


@login_required
@admin_required
def document_detail(request, pk):
    document = get_object_or_404(
        FiscalDocument.objects.select_related(
            "booking",
            "booking__client",
            "booking__employee",
            "booking__service",
            "booking__zone",
        ).prefetch_related("payments"),
        pk=pk,
    )
    initial_paid_at = timezone.localtime().strftime("%Y-%m-%dT%H:%M")
    initial_amount = document.balance_due or document.total_amount
    can_register_payment = document.balance_due > Decimal("0.00")
    can_register_refund = document.payments_total > Decimal("0.00")
    return render(
        request,
        "documents/document_detail.html",
        {
            "active_section": "documents",
            "document": document,
            "payment_form": PaymentForm(initial={"paid_at": initial_paid_at, "amount": initial_amount}),
            "editing_payment": None,
            "can_register_payment": can_register_payment,
            "can_register_refund": can_register_refund,
        },
    )


@login_required
@admin_required
def document_print(request, pk):
    document = get_object_or_404(
        FiscalDocument.objects.select_related(
            "booking",
            "booking__client",
            "booking__employee",
            "booking__service",
            "booking__zone",
        ),
        pk=pk,
    )
    return render(request, "documents/document_print.html", {"document": document})


@login_required
@admin_required
def document_export_csv(request):
    documents, _filters = _filtered_documents(request)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="anna_documents.csv"'
    response.write("\ufeff")

    writer = csv.writer(response)
    writer.writerow([
        "Número",
        "Tipo",
        "Estado",
        "Fecha emisión",
        "Cliente",
        "Teléfono",
        "Servicio",
        "Fecha servicio",
        "Base",
        "IVA",
        "Total",
    ])

    for document in documents:
        booking = document.booking
        writer.writerow([
            document.number,
            document.get_document_type_display(),
            document.get_status_display(),
            document.issue_date.strftime("%d/%m/%Y"),
            str(booking.client),
            booking.client.phone,
            str(booking.service),
            timezone.localtime(booking.start_at).strftime("%d/%m/%Y %H:%M"),
            document.subtotal_amount,
            document.tax_amount,
            document.total_amount,
        ])

    return response


@login_required
@require_POST
@admin_required
def payment_create(request, document_pk):
    document = get_object_or_404(
        FiscalDocument.objects.select_related("booking", "booking__client"),
        pk=document_pk,
    )
    form = PaymentForm(request.POST)

    paid_at_raw = (request.POST.get("paid_at") or "").strip()
    if paid_at_raw:
        try:
            paid_at_value = timezone.datetime.strptime(paid_at_raw, "%Y-%m-%dT%H:%M")
            closed_date = timezone.localtime(timezone.make_aware(paid_at_value)).date()
            if _is_cashbox_closed(closed_date):
                messages.error(request, "La caja de esa fecha ya está cerrada. No se pueden añadir movimientos.")
                return redirect("documents:detail", pk=document.pk)
        except ValueError:
            pass

    if not form.is_valid():
        messages.error(request, "No se pudo registrar el pago. Revisa los datos.")
        return redirect("documents:detail", pk=document.pk)

    payment = form.save(commit=False)
    payment.fiscal_document = document
    payment.booking = document.booking

    if payment.entry_type == Payment.EntryTypes.PAYMENT and document.balance_due <= Decimal("0.00"):
        messages.error(request, "El documento ya está totalmente cobrado. Usa una devolución si necesitas corregirlo.")
        return redirect("documents:detail", pk=document.pk)

    if (
        payment.entry_type == Payment.EntryTypes.PAYMENT
        and payment.amount > document.balance_due
        and document.balance_due > Decimal("0.00")
    ):
        messages.error(request, "El pago supera el saldo pendiente del documento.")
        return redirect("documents:detail", pk=document.pk)

    if payment.entry_type == Payment.EntryTypes.REFUND and payment.amount > document.payments_total:
        messages.error(request, "La devolución supera lo ya cobrado en el documento.")
        return redirect("documents:detail", pk=document.pk)

    payment.save()
    log_event(
        actor=request.user,
        section="payment",
        action="create",
        instance=payment,
        message=f"{payment.get_entry_type_display()} registrada en {document.number}.",
        metadata={"amount": str(payment.amount), "method": payment.method},
    )
    messages.success(request, f"{payment.get_entry_type_display()} registrada: {payment.amount} € por {payment.get_method_display()}.")
    return redirect("documents:detail", pk=document.pk)


@login_required
@require_POST
@admin_required
def document_quick_refund(request, pk):
    document = get_object_or_404(
        FiscalDocument.objects.select_related("booking", "booking__client").prefetch_related("payments"),
        pk=pk,
    )
    if _is_cashbox_closed(timezone.localdate()):
        messages.error(request, "La caja de hoy ya está cerrada. No se pueden registrar devoluciones.")
        return redirect("documents:detail", pk=document.pk)

    if document.payments_total <= Decimal("0.00"):
        messages.error(request, "No hay cobros registrados para devolver en este documento.")
        return redirect("documents:detail", pk=document.pk)

    amount_raw = (request.POST.get("amount") or "").strip().replace(",", ".")
    method = (request.POST.get("method") or "").strip()
    notes = (request.POST.get("notes") or "").strip()

    if method not in Payment.Methods.values:
        messages.error(request, "Método de devolución no válido.")
        return redirect("documents:detail", pk=document.pk)

    try:
        refund_amount = Decimal(amount_raw)
    except Exception:
        messages.error(request, "Importe de devolución no válido.")
        return redirect("documents:detail", pk=document.pk)

    if refund_amount <= Decimal("0.00"):
        messages.error(request, "La devolución debe ser mayor que cero.")
        return redirect("documents:detail", pk=document.pk)

    if refund_amount > document.payments_total:
        messages.error(request, "La devolución supera lo ya cobrado en el documento.")
        return redirect("documents:detail", pk=document.pk)

    payment = Payment.objects.create(
        fiscal_document=document,
        booking=document.booking,
        entry_type=Payment.EntryTypes.REFUND,
        paid_at=timezone.now(),
        amount=refund_amount,
        method=method,
        notes=notes,
    )
    messages.success(
        request,
        f"Devolución rápida registrada en {document.number}: {payment.amount} € por {payment.get_method_display()}.",
    )
    log_event(
        actor=request.user,
        section="payment",
        action="refund",
        instance=payment,
        message=f"Devolución rápida registrada en {document.number}.",
        metadata={"amount": str(payment.amount), "method": payment.method},
    )
    return redirect("documents:detail", pk=document.pk)


@login_required
@require_POST
@admin_required
def booking_quick_payment(request, booking_pk):
    booking = get_object_or_404(
        Booking.objects.select_related("client", "employee", "service").prefetch_related("fiscal_documents__payments"),
        pk=booking_pk,
    )
    method = (request.POST.get("method") or "").strip()
    if method not in Payment.Methods.values:
        messages.error(request, "Método de pago no válido.")
        return redirect("bookings:list")

    payment_date = timezone.localdate()
    if _is_cashbox_closed(payment_date):
        messages.error(request, "La caja de hoy ya está cerrada. No se pueden registrar cobros rápidos.")
        return redirect("bookings:list")

    document, created = _get_or_create_payment_document(booking)
    balance_due = document.balance_due

    if balance_due <= Decimal("0.00"):
        messages.info(request, f"{document.number} ya está totalmente cobrado.")
        return redirect("bookings:list")

    payment = Payment.objects.create(
        fiscal_document=document,
        booking=booking,
        entry_type=Payment.EntryTypes.PAYMENT,
        paid_at=timezone.now(),
        amount=balance_due,
        method=method,
    )

    if created:
        log_event(
            actor=request.user,
            section="payment",
            action="quick_payment",
            instance=payment,
            message=f"Cobro rápido con recibo nuevo {document.number}.",
            metadata={"amount": str(payment.amount), "method": payment.method},
        )
        messages.success(
            request,
            f"Recibo {document.number} creado y cobrado completo: {payment.amount} € por {payment.get_method_display()}.",
        )
    else:
        log_event(
            actor=request.user,
            section="payment",
            action="quick_payment",
            instance=payment,
            message=f"Cobro rápido registrado en {document.number}.",
            metadata={"amount": str(payment.amount), "method": payment.method},
        )
        messages.success(
            request,
            f"Cobro rápido registrado en {document.number}: {payment.amount} € por {payment.get_method_display()}.",
        )
    return redirect("bookings:list")


@login_required
@admin_required
def cashbox(request):
    selected_date = _parse_cashbox_date(request)
    method_value = request.GET.get("method", "").strip()
    entry_type_value = request.GET.get("entry_type", "").strip()

    payments = Payment.objects.select_related(
        "booking",
        "booking__client",
        "booking__service",
        "fiscal_document",
    ).filter(paid_at__date=selected_date)

    if method_value:
        payments = payments.filter(method=method_value)

    if entry_type_value:
        payments = payments.filter(entry_type=entry_type_value)

    closure = CashClosure.objects.filter(closure_date=selected_date).select_related("closed_by").first()
    payments_list = list(payments)
    payments_total = sum((payment.signed_amount for payment in payments_list), Decimal("0.00"))
    totals_by_method = {
        method: sum((payment.signed_amount for payment in payments_list if payment.method == method), Decimal("0.00"))
        for method, _label in Payment.Methods.choices
    }
    pending_documents = FiscalDocument.objects.select_related(
        "booking",
        "booking__client",
        "booking__service",
    ).prefetch_related("payments")

    pending_documents = _pending_documents(pending_documents)
    pending_total = sum((document.balance_due for document in pending_documents), Decimal("0.00"))
    recent_closures = CashClosure.objects.select_related("closed_by")[:10]

    return render(
        request,
        "documents/cashbox.html",
        {
            "active_section": "cashbox",
            "selected_date": selected_date,
            "payments": payments_list,
            "payments_count": len(payments_list),
            "payments_total": payments_total,
            "totals_by_method": totals_by_method,
            "pending_documents": pending_documents[:12],
            "pending_total": pending_total,
            "cash_closure": closure,
            "payment_method_choices": Payment.Methods.choices,
            "entry_type_choices": Payment.EntryTypes.choices,
            "method_value": method_value,
            "entry_type_value": entry_type_value,
            "recent_closures": recent_closures,
        },
    )


@login_required
@admin_required
def cashbox_print(request):
    selected_date = _parse_cashbox_date(request)
    payments = list(
        Payment.objects.select_related(
            "booking",
            "booking__client",
            "booking__service",
            "fiscal_document",
        ).filter(paid_at__date=selected_date)
    )
    closure = CashClosure.objects.filter(closure_date=selected_date).select_related("closed_by").first()
    totals_by_method = {
        method: sum((payment.signed_amount for payment in payments if payment.method == method), Decimal("0.00"))
        for method, _label in Payment.Methods.choices
    }
    payments_total = sum((payment.signed_amount for payment in payments), Decimal("0.00"))

    return render(
        request,
        "documents/cashbox_print.html",
        {
            "selected_date": selected_date,
            "payments": payments,
            "payments_count": len(payments),
            "payments_total": payments_total,
            "totals_by_method": totals_by_method,
            "cash_closure": closure,
        },
    )


@login_required
@admin_required
def payment_edit(request, pk):
    payment = get_object_or_404(
        Payment.objects.select_related("fiscal_document", "booking", "booking__client"),
        pk=pk,
    )
    if _is_cashbox_closed(timezone.localtime(payment.paid_at).date()):
        messages.error(request, "La caja de este movimiento ya está cerrada. No se puede editar.")
        return redirect("documents:detail", pk=payment.fiscal_document_id)

    document = FiscalDocument.objects.select_related(
        "booking",
        "booking__client",
        "booking__employee",
        "booking__service",
        "booking__zone",
    ).prefetch_related("payments").get(pk=payment.fiscal_document_id)

    if request.method == "POST":
        form = PaymentForm(request.POST, instance=payment)
        if form.is_valid():
            updated_payment = form.save(commit=False)
            updated_payment.fiscal_document = document
            updated_payment.booking = document.booking

            other_payments = [item for item in document.payments.all() if item.pk != payment.pk]
            other_total = sum((item.signed_amount for item in other_payments), Decimal("0.00"))
            projected_total = other_total + updated_payment.signed_amount

            if projected_total > document.total_amount:
                messages.error(request, "El total cobrado no puede superar el total del documento.")
            elif projected_total < Decimal("0.00"):
                messages.error(request, "La devolución total no puede dejar el documento en saldo cobrado negativo.")
            else:
                updated_payment.save()
                log_event(
                    actor=request.user,
                    section="payment",
                    action="update",
                    instance=updated_payment,
                    message=f"Movimiento actualizado en {document.number}.",
                    metadata={"amount": str(updated_payment.amount), "method": updated_payment.method},
                )
                messages.success(request, "Movimiento actualizado correctamente.")
                return redirect("documents:detail", pk=document.pk)
    else:
        form = PaymentForm(
            instance=payment,
            initial={"paid_at": timezone.localtime(payment.paid_at).strftime("%Y-%m-%dT%H:%M")},
        )

    return render(
        request,
        "documents/document_detail.html",
        {
            "active_section": "documents",
            "document": document,
            "payment_form": form,
            "editing_payment": payment,
            "can_register_payment": True,
            "can_register_refund": document.payments_total > Decimal("0.00"),
        },
    )


@login_required
@require_POST
@admin_required
def payment_delete(request, pk):
    payment = get_object_or_404(Payment.objects.select_related("fiscal_document"), pk=pk)
    if _is_cashbox_closed(timezone.localtime(payment.paid_at).date()):
        messages.error(request, "La caja de este movimiento ya está cerrada. No se puede eliminar.")
        return redirect("documents:detail", pk=payment.fiscal_document_id)

    document_pk = payment.fiscal_document_id
    payment_label = str(payment)
    payment.delete()
    log_event(
        actor=request.user,
        section="payment",
        action="delete",
        message=f"Movimiento eliminado: {payment_label}.",
    )
    messages.success(request, "Movimiento eliminado correctamente.")
    return redirect("documents:detail", pk=document_pk)


@login_required
@require_POST
@admin_required
def cashbox_close(request):
    date_str = request.POST.get("date", "").strip()
    notes = request.POST.get("notes", "").strip()

    if not date_str:
        messages.error(request, "Indica una fecha para cerrar caja.")
        return redirect("documents:cashbox")

    try:
        closure_date = timezone.datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        messages.error(request, "Fecha inválida para el cierre de caja.")
        return redirect("documents:cashbox")

    payments = list(Payment.objects.filter(paid_at__date=closure_date))
    totals_by_method = {
        method: sum((payment.signed_amount for payment in payments if payment.method == method), Decimal("0.00"))
        for method, _label in Payment.Methods.choices
    }
    total_amount = sum((payment.signed_amount for payment in payments), Decimal("0.00"))

    closure, created = CashClosure.objects.update_or_create(
        closure_date=closure_date,
        defaults={
            "total_amount": total_amount,
            "cash_amount": totals_by_method[Payment.Methods.CASH],
            "card_amount": totals_by_method[Payment.Methods.CARD],
            "bizum_amount": totals_by_method[Payment.Methods.BIZUM],
            "transfer_amount": totals_by_method[Payment.Methods.TRANSFER],
            "payments_count": len(payments),
            "notes": notes,
            "closed_by": request.user,
        },
    )

    messages.success(
        request,
        "Caja cerrada correctamente." if created else "Cierre de caja actualizado correctamente.",
    )
    log_event(
        actor=request.user,
        section="cashbox",
        action="close" if created else "update_close",
        instance=closure,
        message=f"Cierre de caja {'creado' if created else 'actualizado'} para {closure_date:%d/%m/%Y}.",
        metadata={"payments_count": len(payments), "total_amount": str(total_amount)},
    )
    return redirect(f"{reverse('documents:cashbox')}?date={closure.closure_date:%Y-%m-%d}")


@login_required
@admin_required
def cashbox_export_csv(request):
    selected_date = _parse_cashbox_date(request)
    method_value = request.GET.get("method", "").strip()
    entry_type_value = request.GET.get("entry_type", "").strip()

    payments = Payment.objects.select_related(
        "booking",
        "booking__client",
        "booking__service",
        "fiscal_document",
    ).filter(paid_at__date=selected_date)

    if method_value:
        payments = payments.filter(method=method_value)

    if entry_type_value:
        payments = payments.filter(entry_type=entry_type_value)

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="anna_cashbox_{selected_date:%Y%m%d}.csv"'
    response.write("\ufeff")

    writer = csv.writer(response)
    writer.writerow([
        "Fecha",
        "Hora",
        "Cliente",
        "Documento",
        "Tipo",
        "Metodo",
        "Servicio",
        "Referencia",
        "Importe",
        "Notas",
    ])

    for payment in payments:
        writer.writerow([
            timezone.localtime(payment.paid_at).strftime("%d/%m/%Y"),
            timezone.localtime(payment.paid_at).strftime("%H:%M"),
            str(payment.booking.client),
            payment.fiscal_document.number,
            payment.get_entry_type_display(),
            payment.get_method_display(),
            str(payment.booking.service),
            payment.reference,
            payment.signed_amount,
            payment.notes,
        ])

    return response
