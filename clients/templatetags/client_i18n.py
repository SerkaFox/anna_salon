from django import template

from clients.translation import CLIENT_LANGUAGES, CLIENT_LANGUAGE_SESSION_KEY, normalize_client_language, translate_client

register = template.Library()


@register.simple_tag(takes_context=True)
def client_lang(context):
    request = context.get("request")
    if not request:
        return "es"
    return normalize_client_language(request.session.get(CLIENT_LANGUAGE_SESSION_KEY) or request.COOKIES.get(CLIENT_LANGUAGE_SESSION_KEY))


@register.simple_tag(takes_context=True)
def client_languages(context):
    return CLIENT_LANGUAGES


@register.simple_tag(takes_context=True)
def ct(context, key, **kwargs):
    request = context.get("request")
    language = None
    if request:
        language = request.session.get(CLIENT_LANGUAGE_SESSION_KEY) or request.COOKIES.get(CLIENT_LANGUAGE_SESSION_KEY)
    return translate_client(key, language, **kwargs)
