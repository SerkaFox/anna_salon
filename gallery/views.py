import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.db.models import Q
from django.core.paginator import Paginator
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from accounts.permissions import admin_required
from core.i18n import PUBLIC_LANGUAGES, detect_public_language, public_texts

from .forms import InstagramPostForm
from .instagram_api import sync_instagram_media
from .models import InstagramPost


logger = logging.getLogger(__name__)


def public_gallery(request):
    posts = InstagramPost.objects.filter(active=True).order_by("sort_order", "-created_at", "-id")
    language = detect_public_language(request)
    context = {
        "posts": posts,
        "public_language": language,
        "public_languages": PUBLIC_LANGUAGES,
        "t": public_texts(language),
        "canonical_url": f"{settings.PUBLIC_BASE_URL.rstrip('/')}{reverse('public_gallery')}",
    }
    return render(request, "gallery/public_gallery.html", context)


@login_required
@admin_required
def instagram_post_list(request):
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    posts = InstagramPost.objects.all()
    if query:
        posts = posts.filter(Q(title__icontains=query) | Q(caption__icontains=query) | Q(instagram_url__icontains=query))
    if status == "active":
        posts = posts.filter(active=True)
    elif status == "inactive":
        posts = posts.filter(active=False)
    posts_count = posts.count()
    paginator = Paginator(posts, 12)
    page_obj = paginator.get_page(request.GET.get("page"))
    page_query = request.GET.copy()
    page_query.pop("page", None)
    context = {
        "active_section": "gallery",
        "posts": page_obj.object_list,
        "page_obj": page_obj,
        "page_query": page_query.urlencode(),
        "query": query,
        "status": status,
        "posts_count": posts_count,
    }
    return render(request, "gallery/instagrampost_list.html", context)


@login_required
@admin_required
@require_POST
def instagram_sync(request):
    try:
        result = sync_instagram_media()
    except Exception as exc:
        messages.error(request, f"No se pudo sincronizar Instagram: {exc}")
    else:
        messages.success(
            request,
            f"{result['synced']} publicaciones sincronizadas. "
            f"{result['created']} nuevas, {result['updated']} actualizadas, {result['skipped']} omitidas.",
        )
        for error in result.get("errors", [])[:5]:
            messages.error(request, f"Instagram media {error['media_id']}: {error['error']}")
    return redirect("gallery:list")


def instagram_callback(request):
    return HttpResponse("Instagram OAuth callback endpoint configured.", content_type="text/plain")


@login_required
@admin_required
def instagram_post_create(request):
    if request.method == "POST":
        form = InstagramPostForm(request.POST)
        if form.is_valid():
            post = form.save()
            messages.success(request, f"Post creado: {post}")
            return redirect("gallery:list")
    else:
        form = InstagramPostForm()
    return render(request, "gallery/instagrampost_form.html", {"active_section": "gallery", "form": form, "is_edit": False})


@login_required
@admin_required
def instagram_post_update(request, pk):
    post = get_object_or_404(InstagramPost, pk=pk)
    if request.method == "POST":
        form = InstagramPostForm(request.POST, instance=post)
        if form.is_valid():
            post = form.save()
            messages.success(request, f"Post actualizado: {post}")
            return redirect("gallery:list")
    else:
        form = InstagramPostForm(instance=post)
    return render(request, "gallery/instagrampost_form.html", {"active_section": "gallery", "form": form, "post": post, "is_edit": True})


@login_required
@admin_required
def instagram_post_delete(request, pk):
    post = get_object_or_404(InstagramPost, pk=pk)
    if request.method == "POST":
        if request.POST.get("mode") == "delete":
            label = str(post)
            post.delete()
            messages.success(request, f"Post eliminado: {label}")
        else:
            post.active = False
            post.save(update_fields=["active", "updated_at"])
            messages.success(request, f"Post desactivado: {post}")
        return redirect("gallery:list")
    return render(request, "gallery/instagrampost_confirm_delete.html", {"active_section": "gallery", "post": post})


@csrf_exempt
@require_http_methods(["GET", "POST"])
def instagram_webhook(request):
    if request.method == "GET":
        mode = request.GET.get("hub.mode")
        verify_token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge")
        if mode == "subscribe" and verify_token == settings.INSTAGRAM_WEBHOOK_VERIFY_TOKEN and challenge:
            return HttpResponse(challenge, content_type="text/plain")
        return HttpResponseForbidden("Invalid verification token.")

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        payload = {}
    logger.info("Instagram webhook payload received: %s", payload)
    return JsonResponse({"ok": True})
