from django.conf import settings
from django.core import signing
from django.urls import reverse


DOCUMENT_LINK_MAX_AGE = 30 * 24 * 60 * 60
DOCUMENT_LINK_SALT = "documents.public-print"


def sign_document_id(document_id):
    return signing.TimestampSigner(salt=DOCUMENT_LINK_SALT).sign(str(document_id))


def unsign_document_id(token):
    return int(
        signing.TimestampSigner(salt=DOCUMENT_LINK_SALT).unsign(
            token,
            max_age=DOCUMENT_LINK_MAX_AGE,
        )
    )


def get_public_document_url(document):
    path = reverse("documents:public_print", args=[sign_document_id(document.pk)])
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}{path}"
