from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.db.models import Q
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render

from accounts.permissions import admin_required
from core.i18n import PUBLIC_LANGUAGES, detect_public_language, public_texts

from .forms import InstagramPostForm
from .models import InstagramPost


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
    context = {"active_section": "gallery", "posts": posts, "query": query, "status": status, "posts_count": posts.count()}
    return render(request, "gallery/instagrampost_list.html", context)


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
