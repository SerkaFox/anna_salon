import json
import mimetypes
from os.path import splitext
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.utils.dateparse import parse_datetime

from .models import InstagramPost


GRAPH_API_VERSION = "v21.0"
GRAPH_API_BASE_URL = "https://graph.instagram.com"
MEDIA_FIELDS = "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp"


class InstagramAPIError(RuntimeError):
    pass


def _get_config(access_token=None, account_id=None):
    token = access_token or settings.INSTAGRAM_ACCESS_TOKEN
    instagram_account_id = account_id or settings.INSTAGRAM_ACCOUNT_ID
    if not token:
        raise ImproperlyConfigured("INSTAGRAM_ACCESS_TOKEN no esta configurado.")
    if not instagram_account_id:
        raise ImproperlyConfigured("INSTAGRAM_ACCOUNT_ID no esta configurado.")
    return token, instagram_account_id


def fetch_instagram_media(access_token=None, account_id=None):
    token, instagram_account_id = _get_config(access_token=access_token, account_id=account_id)
    query = urlencode({"fields": MEDIA_FIELDS, "access_token": token})
    url = f"{GRAPH_API_BASE_URL}/{GRAPH_API_VERSION}/{instagram_account_id}/media?{query}"
    media = []

    while url:
        try:
            with urlopen(url, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise InstagramAPIError(f"No se pudo sincronizar Instagram: {exc}") from exc

        if "error" in payload:
            message = payload["error"].get("message") or "Error de Instagram API."
            raise InstagramAPIError(message)

        media.extend(payload.get("data") or [])
        url = (payload.get("paging") or {}).get("next")

    return media


def upsert_instagram_media_item(item):
    media_id = str(item.get("id") or "").strip()
    permalink = (item.get("permalink") or "").strip()
    if not media_id or not permalink:
        return None, False

    defaults = {
        "title": "Post Instagram",
        "instagram_url": permalink,
        "embed_html": "",
        "caption": item.get("caption") or "",
        "media_type": item.get("media_type") or "",
        "media_url": item.get("media_url") or "",
        "thumbnail_url": item.get("thumbnail_url") or "",
        "published_at": parse_datetime(item.get("timestamp") or "") if item.get("timestamp") else None,
        "synced_from_api": True,
        "active": True,
    }
    post, created = InstagramPost.objects.update_or_create(
        instagram_media_id=media_id,
        defaults=defaults,
    )
    _cache_remote_media(post, item)
    return post, created


def _guess_extension(source_url, content_type=""):
    parsed_path = urlparse(source_url or "").path
    extension = splitext(parsed_path)[1].lower()
    if extension and 1 < len(extension) <= 8 and extension[1:].isalnum():
        return extension
    if extension:
        extension = ""
    guessed = mimetypes.guess_extension((content_type or "").split(";", 1)[0].strip())
    return guessed or ".bin"


def _download_remote_file(source_url):
    if not source_url:
        return None, "", ""
    with urlopen(source_url, timeout=30) as response:
        content = response.read()
        content_type = ""
        headers = getattr(response, "headers", None)
        if headers is not None:
            content_type = headers.get_content_type() if hasattr(headers, "get_content_type") else headers.get("Content-Type", "")
    return content, content_type, _guess_extension(source_url, content_type)


def _cache_remote_media(post, item):
    changed_fields = []
    media_id = post.instagram_media_id or "instagram"
    media_url = item.get("media_url") or ""
    thumbnail_url = item.get("thumbnail_url") or ""

    if media_url and not post.cached_media:
        content, content_type, extension = _download_remote_file(media_url)
        filename = f"{media_id}_media{extension}"
        post.cached_media.save(filename, ContentFile(content), save=False)
        changed_fields.append("cached_media")

    if thumbnail_url and not post.cached_thumbnail:
        content, content_type, extension = _download_remote_file(thumbnail_url)
        filename = f"{media_id}_thumb{extension}"
        post.cached_thumbnail.save(filename, ContentFile(content), save=False)
        changed_fields.append("cached_thumbnail")
    elif media_url and post.media_type not in {"VIDEO", "REEL"} and not post.cached_thumbnail:
        content, content_type, extension = _download_remote_file(media_url)
        filename = f"{media_id}_thumb{extension}"
        post.cached_thumbnail.save(filename, ContentFile(content), save=False)
        changed_fields.append("cached_thumbnail")

    if changed_fields:
        post.save(update_fields=changed_fields + ["updated_at"])


def sync_instagram_media(access_token=None, account_id=None):
    media_items = fetch_instagram_media(access_token=access_token, account_id=account_id)
    synced_count = 0
    created_count = 0
    updated_count = 0
    skipped_count = 0
    errors = []

    for item in media_items:
        media_id = str(item.get("id") or "").strip() or "unknown"
        try:
            post, created = upsert_instagram_media_item(item)
        except (ValidationError, Exception) as exc:
            skipped_count += 1
            errors.append({"media_id": media_id, "error": str(exc)})
            continue
        if not post:
            skipped_count += 1
            errors.append({"media_id": media_id, "error": "Media sin id o permalink."})
            continue

        synced_count += 1
        if created:
            created_count += 1
        else:
            updated_count += 1

    return {
        "synced": synced_count,
        "created": created_count,
        "updated": updated_count,
        "skipped": skipped_count,
        "errors": errors,
    }
