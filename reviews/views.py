from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Avg
from django.views.generic import TemplateView

from core.i18n import PUBLIC_LANGUAGES, detect_public_language, public_texts
from .models import GoogleReview, TreatwellReview


class ReviewListView(TemplateView):
    template_name = "reviews/review_list.html"

    def _build_combined(self, rating_filter, service_filter, sort):
        tw_qs = TreatwellReview.objects.all()
        if rating_filter:
            tw_qs = tw_qs.filter(rating=rating_filter)
        if service_filter:
            tw_qs = tw_qs.filter(services__contains=service_filter)

        tw_items = [
            {
                "reviewer_name": r.reviewer_name,
                "sort_date": r.date,
                "display_date": r.date,
                "rating": r.rating,
                "text": r.text,
                "source": "treatwell",
                "photo": "",
                "services": r.services,
                "verified": r.verified,
                "stars_range": range(r.rating),
                "empty_stars_range": range(5 - r.rating),
            }
            for r in tw_qs
        ]

        # Google reviews — skip if service filter is active (they carry no service tags)
        g_items = []
        if not service_filter:
            g_qs = GoogleReview.objects.all()
            if rating_filter:
                g_qs = g_qs.filter(rating=rating_filter)
            g_items = [
                {
                    "reviewer_name": r.reviewer_name,
                    "sort_date": r.published_at.date(),
                    "display_date": r.published_at.date(),
                    "rating": r.rating,
                    "text": r.text,
                    "source": "google",
                    "photo": r.reviewer_photo,
                    "services": [],
                    "verified": True,
                    "stars_range": range(r.rating),
                    "empty_stars_range": range(5 - r.rating),
                }
                for r in g_qs
            ]

        combined = tw_items + g_items

        if sort == "best":
            combined.sort(key=lambda x: (-x["rating"], -x["sort_date"].toordinal()))
        elif sort == "oldest":
            combined.sort(key=lambda x: x["sort_date"])
        else:
            combined.sort(key=lambda x: x["sort_date"], reverse=True)

        return combined

    def get(self, request, *args, **kwargs):
        rating = request.GET.get("rating", "")
        service = request.GET.get("service", "")
        sort = request.GET.get("sort", "recent")

        try:
            rating_filter = int(rating) if rating else None
        except ValueError:
            rating_filter = None

        combined = self._build_combined(rating_filter, service, sort)
        paginator = Paginator(combined, 12)
        page_obj = paginator.get_page(request.GET.get("page", 1))

        # Stats always from the full (unfiltered) sets
        all_tw = TreatwellReview.objects.all()
        all_g = GoogleReview.objects.all()
        tw_count = all_tw.count()
        g_count = all_g.count()
        total_count = tw_count + g_count

        tw_avg = all_tw.aggregate(avg=Avg("rating"))["avg"] or 0
        g_avg = all_g.aggregate(avg=Avg("rating"))["avg"] or 0
        if total_count:
            combined_avg = (tw_avg * tw_count + g_avg * g_count) / total_count
        else:
            combined_avg = 0

        rating_dist = {
            i: all_tw.filter(rating=i).count() + all_g.filter(rating=i).count()
            for i in range(5, 0, -1)
        }

        services = set()
        for svc_list in all_tw.values_list("services", flat=True):
            services.update(svc_list)

        language = detect_public_language(request)
        ctx = {
            "page_obj": page_obj,
            "reviews": page_obj,
            "is_paginated": page_obj.has_other_pages(),
            "total_count": total_count,
            "avg_rating": combined_avg,
            "rating_dist": rating_dist,
            "all_services": sorted(services),
            "current_sort": sort,
            "current_rating": rating,
            "current_service": service,
            "google_review_url": GoogleReview.review_url(),
            "google_rating": g_avg,
            "google_count": g_count or getattr(settings, "GOOGLE_PLACE_REVIEW_COUNT", 91),
            "t": public_texts(language),
            "public_language": language,
            "public_languages": PUBLIC_LANGUAGES,
        }
        return self.render_to_response(ctx)
