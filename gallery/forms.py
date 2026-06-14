from django import forms
from django.db.models import Max
from django.utils import timezone

from .models import InstagramPost, normalize_instagram_url, sanitize_instagram_embed


def build_instagram_embed(instagram_url):
    return (
        '<blockquote class="instagram-media" '
        f'data-instgrm-permalink="{instagram_url}" '
        'data-instgrm-version="14">'
        f'<a href="{instagram_url}" target="_blank" rel="noopener">Instagram</a>'
        "</blockquote>"
    )


class InstagramPostForm(forms.ModelForm):
    pasted_input = forms.CharField(
        label="Código de inserción o URL de Instagram",
        widget=forms.Textarea(
            attrs={
                "class": "textarea textarea-large",
                "rows": 10,
                "placeholder": "Pega aquí el código embed de Instagram o la URL del post/reel...",
            }
        ),
        error_messages={"required": "Pega el código de inserción o la URL de Instagram."},
    )

    class Meta:
        model = InstagramPost
        fields = ["pasted_input", "active", "featured"]
        widgets = {
            "active": forms.CheckboxInput(attrs={"class": "checkbox"}),
            "featured": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["active"].label = "Visible en la web"
        self.fields["featured"].label = "Mostrar en portada"
        if self.instance and self.instance.pk and not self.is_bound:
            self.fields["pasted_input"].initial = self.instance.embed_html or self.instance.instagram_url

    def clean_pasted_input(self):
        value = (self.cleaned_data["pasted_input"] or "").strip()
        if "<blockquote" in value.lower():
            embed_html, instagram_url = sanitize_instagram_embed(value)
            self.cleaned_data["_parsed_embed_html"] = embed_html
            self.cleaned_data["_parsed_instagram_url"] = instagram_url
            return value

        if value.startswith("instagram.com/") or value.startswith("www.instagram.com/"):
            value = f"https://{value}"
        instagram_url = normalize_instagram_url(value)
        self.cleaned_data["_parsed_instagram_url"] = instagram_url
        self.cleaned_data["_parsed_embed_html"] = build_instagram_embed(instagram_url)
        return value

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.instagram_url = self.cleaned_data["_parsed_instagram_url"]
        instance.embed_html = self.cleaned_data["_parsed_embed_html"]
        if not instance.title:
            instance.title = f"Post Instagram {timezone.localdate().strftime('%d/%m/%Y')}"
        if not instance.pk and not instance.sort_order:
            next_order = (InstagramPost.objects.aggregate(value=Max("sort_order"))["value"] or 0) + 1
            instance.sort_order = next_order
        if commit:
            instance.save()
        return instance
