import csv
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from bookings.models import Booking

from .forms import PaymentForm
from .models import FiscalDocument, Payment


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

    if payment.amount > document.balance_due and document.balance_due > Decimal("0.00"):
        messages.error(request, "El pago supera el saldo pendiente del documento.")
        return redirect("documents:detail", pk=document.pk)

    payment.save()
    messages.success(request, f"Pago registrado: {payment.amount} € por {payment.get_method_display()}.")
    return redirect("documents:detail", pk=document.pk)


@login_required
def cashbox(request):
    date_value = request.GET.get("date", "").strip()
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

    totals = payments.aggregate(total=Sum("amount"))
    totals_by_method = {
        method: payments.filter(method=method).aggregate(total=Sum("amount")).get("total") or Decimal("0.00")
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
            "payments": payments,
            "payments_count": payments.count(),
            "payments_total": totals.get("total") or Decimal("0.00"),
            "totals_by_method": totals_by_method,
            "pending_documents": pending_documents[:12],
            "pending_total": pending_total,
        },
    )
