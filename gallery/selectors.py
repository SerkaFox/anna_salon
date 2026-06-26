import random

from .models import InstagramPost


def get_public_instagram_posts(limit=None):
    synced_posts = list(
        InstagramPost.objects.filter(active=True, synced_from_api=True).order_by(
            "-featured",
            "sort_order",
            "-published_at",
            "-created_at",
            "-id",
        )
    )
    source = "api"
    if not synced_posts:
        synced_posts = list(
            InstagramPost.objects.filter(active=True).order_by(
                "-featured",
                "sort_order",
                "-published_at",
                "-created_at",
                "-id",
            )
        )
        source = "manual"

    random.shuffle(synced_posts)

    if limit:
        synced_posts = synced_posts[:limit]
    return synced_posts, source
