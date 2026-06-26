import json
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User

from .instagram_api import _guess_extension, sync_instagram_media
from .models import InstagramPost


VALID_EMBED = """
<blockquote class="instagram-media" data-instgrm-permalink="https://www.instagram.com/p/C6gTZD5NAFJ/">
  <a href="https://www.instagram.com/p/C6gTZD5NAFJ/">Instagram</a>
</blockquote>
<script async src="//www.instagram.com/embed.js"></script>
"""


class MockGraphResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def graph_media_payload(caption="Nueva publicacion", media_url="https://cdn.example.com/media.jpg"):
    return {
        "data": [
            {
                "id": "17890000000000001",
                "caption": caption,
                "media_type": "IMAGE",
                "media_url": media_url,
                "thumbnail_url": "https://cdn.example.com/thumb.jpg",
                "permalink": "https://www.instagram.com/p/api-post/",
                "timestamp": "2026-06-15T10:00:00+0000",
            }
        ]
    }


class InstagramPostModelTests(TestCase):
    def test_guess_extension_ignores_signed_url_suffix(self):
        extension = _guess_extension(
            "https://example.com/video.mp4/very/long/signed/token.with.dots.that.should.not_become_extension",
            "video/mp4",
        )

        self.assertEqual(extension, ".mp4")

    def test_model_creation_with_url(self):
        post = InstagramPost.objects.create(instagram_url="https://www.instagram.com/p/C6gTZD5NAFJ/")

        self.assertEqual(post.instagram_url, "https://www.instagram.com/p/C6gTZD5NAFJ/")
        self.assertTrue(post.active)

    def test_instagram_url_validation(self):
        post = InstagramPost(instagram_url="https://example.com/p/C6gTZD5NAFJ/")

        with self.assertRaises(ValidationError):
            post.full_clean()

    def test_script_stripping_and_permalink_extraction(self):
        post = InstagramPost.objects.create(
            instagram_url="https://www.instagram.com/p/placeholder/",
            embed_html=VALID_EMBED,
        )

        self.assertNotIn("<script", post.embed_html)
        self.assertIn("instagram-media", post.embed_html)
        self.assertEqual(post.instagram_url, "https://www.instagram.com/p/C6gTZD5NAFJ/")

    def test_rejects_non_blockquote_embed(self):
        post = InstagramPost(instagram_url="https://www.instagram.com/p/C6gTZD5NAFJ/", embed_html="<div>bad</div>")

        with self.assertRaises(ValidationError):
            post.full_clean()


@override_settings(INSTAGRAM_ACCESS_TOKEN="test-token", INSTAGRAM_ACCOUNT_ID="17841425950738982")
class InstagramAPISyncTests(TestCase):
    @patch("gallery.instagram_api.urlopen")
    def test_sync_creates_posts(self, mocked_urlopen):
        mocked_urlopen.return_value = MockGraphResponse(graph_media_payload())

        result = sync_instagram_media()

        self.assertEqual(result["synced"], 1)
        post = InstagramPost.objects.get(instagram_media_id="17890000000000001")
        self.assertEqual(post.instagram_url, "https://www.instagram.com/p/api-post/")
        self.assertEqual(post.caption, "Nueva publicacion")
        self.assertEqual(post.media_type, "IMAGE")
        self.assertTrue(post.synced_from_api)
        self.assertTrue(post.active)

    @patch("gallery.instagram_api.urlopen")
    def test_sync_updates_existing_posts_by_media_id(self, mocked_urlopen):
        InstagramPost.objects.create(
            instagram_media_id="17890000000000001",
            instagram_url="https://www.instagram.com/p/api-post/",
            caption="Anterior",
            synced_from_api=True,
        )
        mocked_urlopen.return_value = MockGraphResponse(graph_media_payload(caption="Actualizada"))

        result = sync_instagram_media()

        self.assertEqual(result["updated"], 1)
        self.assertEqual(InstagramPost.objects.count(), 1)
        self.assertEqual(InstagramPost.objects.get().caption, "Actualizada")

    @patch("gallery.instagram_api.urlopen")
    def test_sync_does_not_delete_manual_posts(self, mocked_urlopen):
        manual = InstagramPost.objects.create(instagram_url="https://www.instagram.com/p/manual/")
        mocked_urlopen.return_value = MockGraphResponse(graph_media_payload())

        sync_instagram_media()

        self.assertTrue(InstagramPost.objects.filter(pk=manual.pk).exists())
        self.assertEqual(InstagramPost.objects.count(), 2)

    @patch("gallery.instagram_api.upsert_instagram_media_item")
    @patch("gallery.instagram_api.fetch_instagram_media")
    def test_sync_skips_invalid_item_and_continues(self, mocked_fetch, mocked_upsert):
        mocked_fetch.return_value = [{"id": "bad-media"}, {"id": "good-media"}]
        good_post = InstagramPost(instagram_media_id="good-media", instagram_url="https://www.instagram.com/p/good/")
        mocked_upsert.side_effect = [ValidationError("bad media"), (good_post, True)]

        result = sync_instagram_media()

        self.assertEqual(result["synced"], 1)
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["errors"][0]["media_id"], "bad-media")


class InstagramGalleryViewTests(TestCase):
    def setUp(self):
        self.active_post = InstagramPost.objects.create(
            title="Active",
            instagram_url="https://www.instagram.com/p/active/",
            active=True,
        )
        self.inactive_post = InstagramPost.objects.create(
            title="Inactive",
            instagram_url="https://www.instagram.com/p/inactive/",
            active=False,
        )

    def test_public_gallery_displays_active_posts(self):
        response = self.client.get(reverse("public_gallery"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Active")
        self.assertNotContains(response, "Inactive")

    def test_homepage_gallery_uses_active_instagram_posts(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Active")
        self.assertNotContains(response, "Inactive")

    def test_synced_image_renders_native_img(self):
        InstagramPost.objects.create(
            instagram_media_id="image-media",
            instagram_url="https://www.instagram.com/p/native-image/",
            caption="Native image",
            media_type="IMAGE",
            media_url="https://cdn.example.com/native-image.jpg",
            synced_from_api=True,
            active=True,
        )

        response = self.client.get(reverse("public_gallery"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<img', html=False)
        self.assertContains(response, "https://cdn.example.com/native-image.jpg")
        self.assertNotContains(response, "instagram-media")

    def test_synced_image_prefers_cached_media_file(self):
        post = InstagramPost.objects.create(
            instagram_media_id="cached-image-media",
            instagram_url="https://www.instagram.com/p/native-cached-image/",
            caption="Native cached image",
            media_type="IMAGE",
            media_url="https://cdn.example.com/native-cached-image.jpg",
            synced_from_api=True,
            active=True,
        )
        post.cached_thumbnail.save("cached-thumb.jpg", SimpleUploadedFile("cached-thumb.jpg", b"fake-image", content_type="image/jpeg"))

        response = self.client.get(reverse("public_gallery"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'src="/media/instagram_cache/', html=False)

    def test_synced_video_renders_native_video(self):
        InstagramPost.objects.create(
            instagram_media_id="video-media",
            instagram_url="https://www.instagram.com/reel/native-video/",
            caption="Native video",
            media_type="VIDEO",
            media_url="https://cdn.example.com/native-video.mp4",
            synced_from_api=True,
            active=True,
        )

        response = self.client.get(reverse("public_gallery"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<video", html=False)
        self.assertContains(response, "https://cdn.example.com/native-video.mp4")
        self.assertNotContains(response, "instagram-media")

    def test_manual_embeds_hidden_when_synced_posts_exist(self):
        InstagramPost.objects.create(
            title="Manual embed",
            instagram_url="https://www.instagram.com/p/manual-embed/",
            embed_html=VALID_EMBED,
            active=True,
        )
        InstagramPost.objects.create(
            instagram_media_id="api-media",
            instagram_url="https://www.instagram.com/p/api-visible/",
            caption="API visible",
            media_type="IMAGE",
            media_url="https://cdn.example.com/api-visible.jpg",
            synced_from_api=True,
            active=True,
        )

        response = self.client.get(reverse("public_gallery"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "API visible")
        self.assertNotContains(response, "Manual embed")
        self.assertNotContains(response, "C6gTZD5NAFJ")

    def test_inactive_synced_posts_are_hidden(self):
        InstagramPost.objects.create(
            instagram_media_id="hidden-media",
            instagram_url="https://www.instagram.com/p/hidden-api/",
            caption="Hidden API",
            media_type="IMAGE",
            media_url="https://cdn.example.com/hidden.jpg",
            synced_from_api=True,
            active=False,
        )
        InstagramPost.objects.create(
            instagram_media_id="visible-media",
            instagram_url="https://www.instagram.com/p/visible-api/",
            caption="Visible API",
            media_type="IMAGE",
            media_url="https://cdn.example.com/visible.jpg",
            synced_from_api=True,
            active=True,
        )

        response = self.client.get(reverse("public_gallery"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Visible API")
        self.assertNotContains(response, "Hidden API")

    def test_homepage_gallery_falls_back_to_static_images(self):
        InstagramPost.objects.all().delete()

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manicura%2C%20extensiones%20y%20tratamientos.png")
        self.assertContains(response, "Definici%C3%B3n%2C%20depilaci%C3%B3n%20y%20lifting%20de%20cejas.png")

    def test_panel_requires_login(self):
        response = self.client.get(reverse("gallery:list"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_panel_allows_owner(self):
        owner = User.objects.create_user(username="owner", password="testpass123", role=User.ROLE_OWNER)
        self.client.force_login(owner)

        response = self.client.get(reverse("gallery:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Galería Instagram")

    def test_panel_create_accepts_single_url(self):
        owner = User.objects.create_user(username="owner2", password="testpass123", role=User.ROLE_OWNER)
        self.client.force_login(owner)

        response = self.client.post(
            reverse("gallery:create"),
            {
                "pasted_input": "https://www.instagram.com/p/from-panel/",
                "active": "on",
                "featured": "on",
            },
        )

        self.assertRedirects(response, reverse("gallery:list"))
        post = InstagramPost.objects.get(instagram_url="https://www.instagram.com/p/from-panel/")
        self.assertEqual(post.title[:14], "Post Instagram")
        self.assertIn("instagram-media", post.embed_html)
        self.assertTrue(post.active)
        self.assertTrue(post.featured)

    @override_settings(INSTAGRAM_WEBHOOK_VERIFY_TOKEN="verify-token")
    def test_webhook_verification_succeeds(self):
        response = self.client.get(
            reverse("instagram_webhook"),
            {"hub.mode": "subscribe", "hub.verify_token": "verify-token", "hub.challenge": "abc123"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "abc123")

    @override_settings(INSTAGRAM_WEBHOOK_VERIFY_TOKEN="verify-token")
    def test_webhook_verification_fails(self):
        response = self.client.get(
            reverse("instagram_webhook"),
            {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "abc123"},
        )

        self.assertEqual(response.status_code, 403)

    def test_sync_endpoint_requires_login(self):
        response = self.client.post(reverse("gallery:instagram_sync"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_sync_endpoint_rejects_non_admin_user(self):
        user = User.objects.create_user(username="client-sync", password="testpass123", role=User.ROLE_CLIENT)
        self.client.force_login(user)

        response = self.client.post(reverse("gallery:instagram_sync"))

        self.assertEqual(response.status_code, 403)

    @patch("gallery.views.sync_instagram_media", return_value={"synced": 2, "created": 2, "updated": 0, "skipped": 0, "errors": []})
    def test_sync_endpoint_allows_owner(self, mocked_sync):
        owner = User.objects.create_user(username="sync-owner", password="testpass123", role=User.ROLE_OWNER)
        self.client.force_login(owner)

        response = self.client.post(reverse("gallery:instagram_sync"))

        self.assertRedirects(response, reverse("gallery:list"))
        mocked_sync.assert_called_once_with()

    def test_deactivate_manual_posts_action(self):
        owner = User.objects.create_user(username="manual-owner", password="testpass123", role=User.ROLE_OWNER)
        self.client.force_login(owner)
        manual = InstagramPost.objects.create(
            instagram_url="https://www.instagram.com/p/manual-deactivate/",
            embed_html=VALID_EMBED,
            active=True,
        )
        api_post = InstagramPost.objects.create(
            instagram_media_id="api-stays-active",
            instagram_url="https://www.instagram.com/p/api-stays-active/",
            media_type="IMAGE",
            media_url="https://cdn.example.com/api.jpg",
            synced_from_api=True,
            active=True,
        )

        response = self.client.post(reverse("gallery:deactivate_manual"))

        self.assertRedirects(response, reverse("gallery:list"))
        manual.refresh_from_db()
        api_post.refresh_from_db()
        self.assertFalse(manual.active)
        self.assertTrue(api_post.active)

    def test_oauth_callback_route_is_public(self):
        response = self.client.get(reverse("gallery:instagram_callback"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Instagram OAuth callback")
