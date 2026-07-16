from django.conf import settings
from django.db.models import Avg, Count, Q
from django.views.generic import ListView

from .models import GoogleReview, TreatwellReview


class ReviewListView(ListView):
    model = TreatwellReview
    template_name = "reviews/review_list.html"
    context_object_name = "reviews"
    paginate_by = 12

    def get_queryset(self):
        qs = TreatwellReview.objects.all()
        rating = self.request.GET.get("rating")
        service = self.request.GET.get("service")
        sort = self.request.GET.get("sort", "recent")

        if rating:
            try:
                qs = qs.filter(rating=int(rating))
            except ValueError:
                pass
        if service:
            qs = qs.filter(services__contains=service)
        if sort == "best":
            qs = qs.order_by("-rating", "-date")
        elif sort == "oldest":
            qs = qs.order_by("date")
        else:
            qs = qs.order_by("-date")
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        all_reviews = TreatwellReview.objects.all()
        ctx["total_count"] = all_reviews.count()
        ctx["avg_rating"] = all_reviews.aggregate(avg=Avg("rating"))["avg"] or 0
        ctx["rating_dist"] = {
            i: all_reviews.filter(rating=i).count() for i in range(5, 0, -1)
        }
        # All unique services for filter dropdown
        services = set()
        for r in all_reviews.values_list("services", flat=True):
            services.update(r)
        ctx["all_services"] = sorted(services)
        ctx["current_sort"] = self.request.GET.get("sort", "recent")
        ctx["current_rating"] = self.request.GET.get("rating", "")
        ctx["current_service"] = self.request.GET.get("service", "")
        ctx["google_reviews"] = GoogleReview.objects.all()[:5]
        ctx["google_review_url"] = GoogleReview.review_url()
        ctx["google_rating"] = GoogleReview.objects.aggregate(avg=Avg("rating"))["avg"] or 0
        ctx["google_count"] = getattr(settings, "GOOGLE_PLACE_REVIEW_COUNT", 91)
        return ctx
