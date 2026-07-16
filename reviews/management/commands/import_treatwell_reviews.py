import json
import os
from datetime import date

from django.core.management.base import BaseCommand

from reviews.models import TreatwellReview


class Command(BaseCommand):
    help = "Import Treatwell reviews from treatwell_reviews.json"

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            default="treatwell_reviews.json",
            help="Path to the JSON file",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete existing reviews before import",
        )

    def handle(self, *args, **options):
        path = options["file"]
        if not os.path.exists(path):
            self.stderr.write(f"File not found: {path}")
            return

        if options["clear"]:
            deleted, _ = TreatwellReview.objects.all().delete()
            self.stdout.write(f"Deleted {deleted} existing reviews.")

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        created = updated = skipped = 0
        for r in data:
            if not r.get("text", "").strip():
                skipped += 1
                continue

            try:
                date_val = date.fromisoformat(r["date"])
            except (KeyError, ValueError):
                skipped += 1
                continue

            tid = r.get("id") if isinstance(r.get("id"), int) and r["id"] > 0 else None

            defaults = {
                "reviewer_name": r.get("name", "Anónimo")[:120],
                "date": date_val,
                "rating": int(r.get("rating", 5)),
                "text": r.get("text", "").strip(),
                "services": r.get("services", []),
                "employee": r.get("employee", "")[:200],
                "verified": bool(r.get("verified", False)),
            }

            if tid:
                obj, was_created = TreatwellReview.objects.update_or_create(
                    treatwell_id=tid, defaults=defaults
                )
                if was_created:
                    created += 1
                else:
                    updated += 1
            else:
                # No Treatwell ID — match on name+date+text to avoid dupes
                obj, was_created = TreatwellReview.objects.get_or_create(
                    reviewer_name=defaults["reviewer_name"],
                    date=date_val,
                    text=defaults["text"],
                    defaults={**defaults, "treatwell_id": None},
                )
                if was_created:
                    created += 1
                else:
                    skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {created} created, {updated} updated, {skipped} skipped. "
                f"Total in DB: {TreatwellReview.objects.count()}"
            )
        )
