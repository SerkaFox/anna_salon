from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.db import models


ALLOWED_INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com"}


def normalize_instagram_url(value):
    value = (value or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    host = parsed.netloc.lower()
    if host not in ALLOWED_INSTAGRAM_HOSTS:
        raise ValidationError("Introduce una URL valida de Instagram.")
    if not parsed.scheme:
        raise ValidationError("Introduce una URL completa de Instagram.")
    if parsed.scheme not in {"http", "https"}:
        raise ValidationError("La URL de Instagram debe usar http o https.")
    return value


class InstagramBlockquoteParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.seen_root = False
        self.valid_root = False
        self.permalink = ""
        self.parts = []
        self.disallowed = False
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs_dict = {key.lower(): value for key, value in attrs}

        if tag == "script":
            self.skip_depth += 1
            return
        if self.skip_depth:
            return

        if not self.seen_root:
            self.seen_root = True
            classes = set((attrs_dict.get("class") or "").split())
            if tag != "blockquote" or "instagram-media" not in classes:
                self.disallowed = True
                return
            self.valid_root = True
            self.permalink = attrs_dict.get("data-instgrm-permalink") or ""
        elif self.depth <= 0:
            self.disallowed = True
            return

        self.depth += 1
        safe_attrs = []
        for key, value in attrs:
            key = key.lower()
            if key.startswith("on"):
                continue
            if tag == "blockquote":
                allowed = key in {
                    "class",
                    "data-instgrm-permalink",
                    "data-instgrm-version",
                    "style",
                } or key.startswith("data-")
            else:
                allowed = key in {"href", "style", "target", "rel", "class", "title", "aria-label"}
            if not allowed:
                continue
            if key == "href" and value and urlparse(value).scheme not in {"http", "https", ""}:
                continue
            safe_attrs.append(f'{key}="{escape(value or "", quote=True)}"')
        attrs_text = f" {' '.join(safe_attrs)}" if safe_attrs else ""
        self.parts.append(f"<{tag}{attrs_text}>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "script":
            if self.skip_depth:
                self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if self.depth > 0:
            self.parts.append(f"</{tag}>")
            self.depth -= 1

    def handle_data(self, data):
        if self.skip_depth or not self.valid_root:
            return
        self.parts.append(escape(data))

    def handle_entityref(self, name):
        if self.skip_depth or not self.valid_root:
            return
        self.parts.append(f"&{name};")

    def handle_charref(self, name):
        if self.skip_depth or not self.valid_root:
            return
        self.parts.append(f"&#{name};")


def sanitize_instagram_embed(value):
    value = (value or "").strip()
    if not value:
        return "", ""
    parser = InstagramBlockquoteParser()
    parser.feed(value)
    parser.close()
    if parser.disallowed or not parser.valid_root:
        raise ValidationError("El embed debe contener solo un blockquote de Instagram.")
    sanitized = "".join(parser.parts).strip()
    permalink = (parser.permalink or "").strip()
    if permalink:
        normalize_instagram_url(permalink)
    return sanitized, permalink


class InstagramPost(models.Model):
    title = models.CharField("Titulo", max_length=160, blank=True)
    instagram_media_id = models.CharField("ID de media Instagram", max_length=80, blank=True, null=True, unique=True)
    instagram_url = models.URLField("URL de Instagram")
    embed_html = models.TextField("Embed blockquote", blank=True)
    caption = models.TextField("Texto", blank=True)
    media_type = models.CharField("Tipo de media", max_length=40, blank=True)
    media_url = models.URLField("URL de media", max_length=1000, blank=True)
    thumbnail_url = models.URLField("URL de miniatura", max_length=1000, blank=True)
    published_at = models.DateTimeField("Publicado en Instagram", null=True, blank=True)
    synced_from_api = models.BooleanField("Sincronizado por API", default=False)
    active = models.BooleanField("Activo", default=True)
    featured = models.BooleanField("Destacado", default=False)
    sort_order = models.PositiveIntegerField("Orden", default=0)
    created_at = models.DateTimeField("Creado", auto_now_add=True)
    updated_at = models.DateTimeField("Actualizado", auto_now=True)

    class Meta:
        ordering = ["sort_order", "-created_at", "-id"]
        verbose_name = "Post de Instagram"
        verbose_name_plural = "Posts de Instagram"

    def clean(self):
        super().clean()
        sanitized, permalink = sanitize_instagram_embed(self.embed_html)
        if permalink:
            self.instagram_url = permalink
        self.instagram_url = normalize_instagram_url(self.instagram_url)
        self.embed_html = sanitized

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title or self.instagram_url
