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

from bookings.models import Booking

from .forms import PaymentForm
from .models import CashClosure, FiscalDocument, Payment


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


@login_required
def document_list(request):
    documents, filters = _filtered_documents(request)
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
            "totals": totals,
            "document_type_choices": FiscalDocument.DocumentTypes.choices,
            "status_choices": FiscalDocument.Statuses.choices,
            "payment_method_choices": Payment.Methods.choices,
            **filters,
        },
    )


@login_required
@require_POST
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
        messages.success(request, f"Documento creado: {document}.")

    return redirect("documents:detail", pk=document.pk)


@login_required
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
    return render(
        request,
        "documents/document_detail.html",
        {
            "active_section": "documents",
            "document": document,
            "payment_form": PaymentForm(initial={"paid_at": initial_paid_at, "amount": initial_amount}),
            "editing_payment": None,
        },
    )


@login_required
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
def payment_create(request, document_pk):
    document = get_object_or_404(
        FiscalDocument.objects.select_related("booking", "booking__client"),
        pk=document_pk,
    )
    form = PaymentForm(request.POST)

    if not form.is_valid():
        messages.error(request, "No se pudo registrar el pago. Revisa los datos.")
        return redirect("documents:detail", pk=document.pk)

    payment = form.save(commit=False)
    payment.fiscal_document = document
    payment.booking = document.booking

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
    messages.success(request, f"{payment.get_entry_type_display()} registrada: {payment.amount} € por {payment.get_method_display()}.")
    return redirect("documents:detail", pk=document.pk)


@login_required
def cashbox(request):
    date_value = request.GET.get("date", "").strip()
    method_value = request.GET.get("method", "").strip()
    entry_type_value = request.GET.get("entry_type", "").strip()
    if date_value:
        try:
            selected_date = timezone.datetime.strptime(date_value, "%Y-%m-%d").date()
        except ValueError:
            selected_date = timezone.localdate()
    else:
        selected_date = timezone.localdate()

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

    pending_documents = [document for document in pending_documents if document.balance_due > Decimal("0.00")]
    pending_total = sum((document.balance_due for document in pending_documents), Decimal("0.00"))

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
        },
    )


@login_required
def payment_edit(request, pk):
    payment = get_object_or_404(
        Payment.objects.select_related("fiscal_document", "booking", "booking__client"),
        pk=pk,
    )
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
        },
    )


@login_required
@require_POST
def payment_delete(request, pk):
    payment = get_object_or_404(Payment.objects.select_related("fiscal_document"), pk=pk)
    document_pk = payment.fiscal_document_id
    payment.delete()
    messages.success(request, "Movimiento eliminado correctamente.")
    return redirect("documents:detail", pk=document_pk)


@login_required
@require_POST
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
    return redirect(f"{reverse('documents:cashbox')}?date={closure.closure_date:%Y-%m-%d}")
