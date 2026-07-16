"""Sync Google Places reviews into GoogleReview model."""

import urllib.request
import urllib.parse
import json
from django.conf import settings
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from .models import GoogleReview


PLACES_URL = "https://places.googleapis.com/v1/places/{place_id}"
FIELDS = "reviews,rating,userRatingCount"


def fetch_google_reviews():
    api_key = getattr(settings, "GOOGLE_PLACES_API_KEY", "")
    place_id = getattr(settings, "GOOGLE_PLACE_ID", "")
    if not api_key or not place_id:
        return []

    url = f"{PLACES_URL.format(place_id=place_id)}?fields={FIELDS}&languageCode=es"
    req = urllib.request.Request(
        url,
        headers={
            "X-Goog-Api-Key": api_key,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def sync_google_reviews():
    data = fetch_google_reviews()
    reviews = data.get("reviews", [])
    created = updated = 0
    for r in reviews:
        author = r.get("authorAttribution", {})
        name = author.get("displayName", "Anónimo")
        author_url = author.get("uri", "")
        photo_url = author.get("photoUri", "")
        rating = r.get("rating", 5)
        text = r.get("text", {}).get("text", "")
        publish_time = r.get("publishTime", "")
        google_review_id = r.get("name", "")  # e.g. "places/XXX/reviews/YYY"

        try:
            published_at = parse_datetime(publish_time) if publish_time else timezone.now()
        except Exception:
            published_at = timezone.now()

        obj, was_created = GoogleReview.objects.update_or_create(
            google_review_id=google_review_id,
            defaults={
                "reviewer_name": name[:120],
                "reviewer_url": author_url[:500],
                "reviewer_photo": photo_url[:500],
                "rating": rating,
                "text": text,
                "published_at": published_at,
            },
        )
        if was_created:
            created += 1
        else:
            updated += 1

    return {"created": created, "updated": updated, "total": GoogleReview.objects.count()}
