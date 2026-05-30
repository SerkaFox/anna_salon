from django.db import transaction
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.http import HttpResponseBadRequest, HttpResponse

from .models import Payment
from .redsys import RedsysSignatureError, is_successful_response, sanitize_redsys_payload, verify_signature


@csrf_exempt
@require_POST
def redsys_notification(request):
    encoded_parameters = request.POST.get("Ds_MerchantParameters", "")
    signature = request.POST.get("Ds_Signature", "")
    if not encoded_parameters or not signature:
        return HttpResponseBadRequest("Missing Redsys parameters.")

    try:
        payload = verify_signature(encoded_parameters, signature)
    except RedsysSignatureError:
        return HttpResponseBadRequest("Invalid Redsys signature.")
    except Exception:
        return HttpResponseBadRequest("Invalid Redsys payload.")

    order_number = payload.get("Ds_Order") or payload.get("DS_MERCHANT_ORDER")
    response_code = str(payload.get("Ds_Response", ""))
    authorisation_code = str(payload.get("Ds_AuthorisationCode", ""))
    safe_payload = sanitize_redsys_payload(payload)

    with transaction.atomic():
        try:
            payment = Payment.objects.select_for_update().get(order_number=order_number)
        except Payment.DoesNotExist:
            return HttpResponseBadRequest("Unknown Redsys order.")

        payment.raw_response = safe_payload
        payment.redsys_response_code = response_code
        payment.redsys_authorisation_code = authorisation_code
        if is_successful_response(response_code):
            payment.status = Payment.Statuses.PAID
            payment.method = Payment.Methods.CARD if payment.method == Payment.Methods.UNKNOWN else payment.method
            if payment.paid_at is None:
                payment.paid_at = timezone.now()
        elif payment.status != Payment.Statuses.PAID:
            payment.status = Payment.Statuses.FAILED
        payment.save(
            update_fields=[
                "raw_response",
                "redsys_response_code",
                "redsys_authorisation_code",
                "status",
                "method",
                "paid_at",
                "updated_at",
            ]
        )

    return HttpResponse("OK")


def redsys_success(request):
    return render(request, "payments/redsys_result.html", {"status": "success"})


def redsys_error(request):
    return render(request, "payments/redsys_result.html", {"status": "error"})
