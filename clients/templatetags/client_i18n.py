from django import template

from clients.translation import CLIENT_LANGUAGES, CLIENT_LANGUAGE_SESSION_KEY, detect_client_language, normalize_client_language, translate_client

register = template.Library()


@register.simple_tag(takes_context=True)
def client_lang(context):
    request = context.get("request")
    if not request:
        return "es"
    return detect_client_language(request)


@register.simple_tag(takes_context=True)
def client_languages(context):
    return CLIENT_LANGUAGES


@register.simple_tag(takes_context=True)
def ct(context, key, **kwargs):
    request = context.get("request")
    language = None
    if request:
        language = detect_client_language(request)
    return translate_client(key, language, **kwargs)


@register.simple_tag(takes_context=True)
def site_chrome(context):
    """Texts and links for the public-site header/footer shown in the portal."""
    from core import views as core_views
    from core.i18n import PUBLIC_LANGUAGES, detect_public_language, public_texts

    request = context.get("request")
    language = detect_public_language(request) if request else "es"
    return {
        "t": public_texts(language),
        "language": language,
        "languages": PUBLIC_LANGUAGES,
        "phone": core_views.SALON_PHONE,
        "whatsapp_url": core_views.SALON_WHATSAPP_URL,
    }
