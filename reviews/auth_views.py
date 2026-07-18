"""Google OAuth views — /panel/google-auth/"""

import secrets

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from accounts.permissions import is_admin_user
from .google_oauth import exchange_code, get_account_and_location, get_auth_url, sync_all_google_reviews
from .models import GoogleOAuthToken


def _callback_uri(request):
    return request.build_absolute_uri(reverse("reviews_auth:google_callback"))


@login_required
def google_auth_start(request):
    if not is_admin_user(request.user):
        return redirect("dashboard:index")
    state = secrets.token_urlsafe(16)
    request.session["google_oauth_state"] = state
    url = get_auth_url(redirect_uri=_callback_uri(request), state=state)
    return redirect(url)


@login_required
@require_GET
def google_auth_callback(request):
    if not is_admin_user(request.user):
        return redirect("dashboard:index")

    error = request.GET.get("error")
    if error:
        return render(request, "reviews/google_auth.html", {"error": error})

    code = request.GET.get("code", "")
    state = request.GET.get("state", "")
    if state != request.session.get("google_oauth_state", ""):
        return render(request, "reviews/google_auth.html", {"error": "Estado inválido (CSRF)."})

    try:
        tokens = exchange_code(code, redirect_uri=_callback_uri(request))
    except Exception as exc:
        return render(request, "reviews/google_auth.html", {"error": str(exc)})

    token_obj, _ = GoogleOAuthToken.objects.get_or_create(pk=1)
    token_obj.access_token = tokens.get("access_token", "")
    token_obj.refresh_token = tokens.get("refresh_token", token_obj.refresh_token)
    from django.utils import timezone
    from datetime import timedelta
    token_obj.token_expiry = timezone.now() + timedelta(seconds=tokens.get("expires_in", 3600))
    token_obj.save()

    # Auto-discover account/location and sync
    try:
        get_account_and_location(token_obj)
        result = sync_all_google_reviews(token_obj)
        return render(request, "reviews/google_auth.html", {
            "success": True,
            "created": result["created"],
            "updated": result["updated"],
            "total": result["total"],
            "account": token_obj.account_name,
            "location": token_obj.location_name,
        })
    except Exception as exc:
        return render(request, "reviews/google_auth.html", {
            "success": True,
            "error_sync": str(exc),
        })


@login_required
def google_auth_status(request):
    if not is_admin_user(request.user):
        return redirect("dashboard:index")
    token_obj = GoogleOAuthToken.get()
    from .models import GoogleReview
    ctx = {
        "token": token_obj,
        "review_count": GoogleReview.objects.count(),
    }
    if request.method == "POST" and token_obj:
        try:
            result = sync_all_google_reviews(token_obj)
            ctx["synced"] = result
        except Exception as exc:
            ctx["sync_error"] = str(exc)
    return render(request, "reviews/google_auth.html", ctx)
