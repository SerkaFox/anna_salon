import re

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

User = get_user_model()


def normalize_phone(value):
    return re.sub(r"\D+", "", (value or "").strip())


def is_email_value(value):
    value = (value or "").strip().lower()
    if "@" not in value:
        return False
    try:
        validate_email(value)
    except ValidationError:
        return False
    return True


def classify_contact(value):
    raw_value = (value or "").strip()
    if not raw_value:
        return None
    if is_email_value(raw_value):
        return {"kind": "email", "value": raw_value.lower()}

    digits = normalize_phone(raw_value)
    if digits and re.fullmatch(r"[+\d\s()\-]+", raw_value):
        return {"kind": "phone", "value": raw_value, "normalized": digits}
    return {"kind": "invalid", "value": raw_value}


def phone_variants_match(left_value, right_value):
    left_digits = normalize_phone(left_value)
    right_digits = normalize_phone(right_value)
    if not left_digits or not right_digits:
        return False
    if left_digits == right_digits:
        return True
    return left_digits.endswith(right_digits) or right_digits.endswith(left_digits)


def resolve_user_by_identity(identity):
    identity_info = classify_contact(identity)
    if not identity_info or identity_info["kind"] == "invalid":
        return User.objects.filter(username__iexact=(identity or "").strip()).first()

    if identity_info["kind"] == "email":
        return User.objects.filter(email__iexact=identity_info["value"]).first()

    for user in User.objects.exclude(phone=""):
        if phone_variants_match(user.phone, identity_info["value"]):
            return user
    return None
