import base64
import hashlib
import hmac
import json
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.urls import reverse

try:
    from Crypto.Cipher import DES3
except ImportError:  # pragma: no cover - exercised only in incomplete deployments.
    DES3 = None


SIGNATURE_VERSION = "HMAC_SHA512_V1"
PAYMENT_URLS = {
    "test": "https://sis-t.redsys.es:25443/sis/realizarPago",
    "prod": "https://sis.redsys.es/sis/realizarPago",
}
SAFE_RESPONSE_FIELDS = {
    "DS_MERCHANT_AMOUNT",
    "DS_MERCHANT_ORDER",
    "DS_MERCHANT_MERCHANTCODE",
    "DS_MERCHANT_CURRENCY",
    "DS_MERCHANT_TRANSACTIONTYPE",
    "DS_MERCHANT_TERMINAL",
    "DS_MERCHANT_MERCHANTURL",
    "DS_MERCHANT_URLOK",
    "DS_MERCHANT_URLKO",
    "DS_MERCHANT_PRODUCTDESCRIPTION",
    "DS_MERCHANT_MERCHANTDATA",
    "Ds_Date",
    "Ds_Hour",
    "Ds_Amount",
    "Ds_Currency",
    "Ds_Order",
    "Ds_MerchantCode",
    "Ds_Terminal",
    "Ds_Response",
    "Ds_AuthorisationCode",
    "Ds_TransactionType",
    "Ds_SecurePayment",
    "Ds_Language",
    "Ds_MerchantData",
    "Ds_ProcessedPayMethod",
}


class RedsysConfigurationError(RuntimeError):
    pass


class RedsysSignatureError(ValueError):
    pass


def get_payment_url():
    return PAYMENT_URLS.get(settings.REDSYS_ENVIRONMENT, PAYMENT_URLS["test"])


def amount_to_cents(amount):
    cents = (Decimal(amount) * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return str(int(cents))


def build_absolute_url(path):
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}{path}"


def sanitize_redsys_payload(payload):
    return {key: value for key, value in payload.items() if key in SAFE_RESPONSE_FIELDS}


def build_merchant_parameters(payment, request=None):
    merchant_url = build_absolute_url(reverse("payments:redsys_notification"))
    success_url = build_absolute_url(reverse("payments:redsys_success"))
    error_url = build_absolute_url(reverse("payments:redsys_error"))

    # Verify final field set and optional Bizum-specific DS_MERCHANT_PAYMETHODS
    # against the Ruralvia/Redsys TPV Virtual documentation before production.
    return {
        "DS_MERCHANT_AMOUNT": amount_to_cents(payment.amount),
        "DS_MERCHANT_ORDER": payment.order_number,
        "DS_MERCHANT_MERCHANTCODE": settings.REDSYS_MERCHANT_CODE,
        "DS_MERCHANT_CURRENCY": payment.currency,
        "DS_MERCHANT_TRANSACTIONTYPE": settings.REDSYS_TRANSACTION_TYPE,
        "DS_MERCHANT_TERMINAL": settings.REDSYS_TERMINAL,
        "DS_MERCHANT_MERCHANTURL": merchant_url,
        "DS_MERCHANT_URLOK": success_url,
        "DS_MERCHANT_URLKO": error_url,
        "DS_MERCHANT_PRODUCTDESCRIPTION": f"Reserva BRIMOON Studio #{payment.booking_id}",
        "DS_MERCHANT_MERCHANTDATA": str(payment.pk),
    }


def build_form_fields(merchant_parameters):
    encoded_parameters = encode_merchant_parameters(merchant_parameters)
    return {
        "Ds_SignatureVersion": SIGNATURE_VERSION,
        "Ds_MerchantParameters": encoded_parameters,
        "Ds_Signature": sign_merchant_parameters(encoded_parameters, merchant_parameters["DS_MERCHANT_ORDER"]),
    }


def encode_merchant_parameters(parameters):
    raw = json.dumps(parameters, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def decode_merchant_parameters(encoded_parameters):
    padded = _with_base64_padding(encoded_parameters)
    raw = base64.b64decode(padded)
    return json.loads(raw.decode("utf-8"))


def sign_merchant_parameters(encoded_parameters, order_number):
    key = _derive_order_key(order_number)
    digest = hmac.new(key, encoded_parameters.encode("ascii"), hashlib.sha512).digest()
    return _urlsafe_base64(digest)


def verify_signature(encoded_parameters, signature):
    parameters = decode_merchant_parameters(encoded_parameters)
    order_number = parameters.get("Ds_Order") or parameters.get("DS_MERCHANT_ORDER")
    if not order_number:
        raise RedsysSignatureError("Redsys payload does not include an order number.")

    expected = sign_merchant_parameters(encoded_parameters, order_number)
    if not hmac.compare_digest(_normalise_signature(signature), _normalise_signature(expected)):
        raise RedsysSignatureError("Invalid Redsys signature.")
    return parameters


def is_successful_response(response_code):
    try:
        return 0 <= int(str(response_code)) <= 99
    except (TypeError, ValueError):
        return False


def _derive_order_key(order_number):
    if DES3 is None:
        raise RedsysConfigurationError("pycryptodome is required for Redsys 3DES key derivation.")
    if not settings.REDSYS_SECRET_KEY:
        raise RedsysConfigurationError("REDSYS_SECRET_KEY is not configured.")

    merchant_key = base64.b64decode(_with_base64_padding(settings.REDSYS_SECRET_KEY))
    cipher = DES3.new(merchant_key, DES3.MODE_CBC, iv=b"\0" * 8)
    order = str(order_number).encode("ascii")
    padded_order = order + (b"\0" * ((8 - len(order) % 8) % 8))
    return cipher.encrypt(padded_order)


def _urlsafe_base64(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _normalise_signature(signature):
    signature = str(signature or "").replace(" ", "+")
    try:
        return _urlsafe_base64(base64.urlsafe_b64decode(_with_base64_padding(signature)))
    except Exception:
        return signature.rstrip("=")


def _with_base64_padding(value):
    value = str(value or "")
    return value + ("=" * ((4 - len(value) % 4) % 4))
