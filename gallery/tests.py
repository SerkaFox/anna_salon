from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import User

from .models import InstagramPost


VALID_EMBED = """
<blockquote class="instagram-media" data-instgrm-permalink="https://www.instagram.com/p/C6gTZD5NAFJ/">
  <a href="https://www.instagram.com/p/C6gTZD5NAFJ/">Instagram</a>
</blockquote>
<script async src="//www.instagram.com/embed.js"></script>
"""


class InstagramPostModelTests(TestCase):
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
        self.assertContains(response, "https://www.instagram.com/embed.js")

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
