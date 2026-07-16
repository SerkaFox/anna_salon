from django.core.management.base import BaseCommand
from reviews.google_sync import sync_google_reviews


class Command(BaseCommand):
    help = "Sync latest Google Places reviews into the DB"

    def handle(self, *args, **options):
        result = sync_google_reviews()
        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {result['created']} created, {result['updated']} updated. "
                f"Total Google reviews in DB: {result['total']}"
            )
        )
