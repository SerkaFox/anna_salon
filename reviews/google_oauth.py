"""Google Business Profile OAuth + reviews sync."""

import json
import urllib.request
import urllib.parse
from datetime import timedelta

from django.conf import settings
from django.utils import timezone


SCOPES = ["https://www.googleapis.com/auth/business.manage"]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ACCOUNTS_URL = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
LOCATIONS_URL = "https://mybusinessinformation.googleapis.com/v1/{account}/locations?readMask=name,title"
REVIEWS_URL = "https://mybusiness.googleapis.com/v4/{location}/reviews?pageSize=50"


def _client_id():
    return getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "")


def _client_secret():
    return getattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", "")


def get_auth_url(redirect_uri, state=""):
    params = {
        "client_id": _client_id(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(code, redirect_uri):
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": _client_id(),
        "client_secret": _client_secret(),
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def refresh_access_token(refresh_token):
    data = urllib.parse.urlencode({
        "refresh_token": refresh_token,
        "client_id": _client_id(),
        "client_secret": _client_secret(),
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _get_valid_token(token_obj):
    """Return a valid access token, refreshing if needed."""
    if token_obj.token_expiry and timezone.now() < token_obj.token_expiry - timedelta(minutes=5):
        return token_obj.access_token
    result = refresh_access_token(token_obj.refresh_token)
    token_obj.access_token = result["access_token"]
    token_obj.token_expiry = timezone.now() + timedelta(seconds=result.get("expires_in", 3600))
    token_obj.save(update_fields=["access_token", "token_expiry", "updated_at"])
    return token_obj.access_token


def _get(url, access_token):
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {access_token}")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def get_account_and_location(token_obj):
    """Auto-discover account + location and save them."""
    access_token = _get_valid_token(token_obj)
    accounts = _get(ACCOUNTS_URL, access_token).get("accounts", [])
    if not accounts:
        raise RuntimeError("No Google Business accounts found.")
    account = accounts[0]["name"]

    locs = _get(LOCATIONS_URL.format(account=account), access_token).get("locations", [])
    if not locs:
        raise RuntimeError(f"No locations found under {account}.")
    location = locs[0]["name"]

    token_obj.account_name = account
    token_obj.location_name = location
    token_obj.save(update_fields=["account_name", "location_name", "updated_at"])
    return account, location


def fetch_all_reviews(token_obj):
    """Fetch all reviews via Business Profile API (paginated)."""
    access_token = _get_valid_token(token_obj)
    location = token_obj.location_name
    if not location:
        get_account_and_location(token_obj)
        access_token = _get_valid_token(token_obj)
        location = token_obj.location_name

    reviews = []
    url = REVIEWS_URL.format(location=location)
    while url:
        data = _get(url, access_token)
        reviews.extend(data.get("reviews", []))
        next_token = data.get("nextPageToken")
        if next_token:
            base = REVIEWS_URL.format(location=location)
            url = f"{base}&pageToken={urllib.parse.quote(next_token)}"
        else:
            url = None
    return reviews


def sync_all_google_reviews(token_obj):
    from .models import GoogleReview
    from django.utils.dateparse import parse_datetime

    raw_reviews = fetch_all_reviews(token_obj)
    created = updated = 0

    for r in raw_reviews:
        reviewer = r.get("reviewer", {})
        name = reviewer.get("displayName", "Anónimo")
        photo = reviewer.get("profilePhotoUrl", "")
        rating = int(r.get("starRating", "FIVE").replace("FIVE", "5").replace("FOUR", "4")
                     .replace("THREE", "3").replace("TWO", "2").replace("ONE", "1"))
        text = r.get("comment", "")
        review_id = r.get("reviewId", "")
        create_time = r.get("createTime", "")

        try:
            published_at = parse_datetime(create_time) if create_time else timezone.now()
        except Exception:
            published_at = timezone.now()

        obj, was_created = GoogleReview.objects.update_or_create(
            google_review_id=review_id,
            defaults={
                "reviewer_name": name[:120],
                "reviewer_url": "",
                "reviewer_photo": photo[:500],
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
