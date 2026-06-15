from django.core.management.base import BaseCommand, CommandError

from gallery.instagram_api import sync_instagram_media


class Command(BaseCommand):
    help = "Synchronize Instagram media from the configured Meta Graph API account."

    def handle(self, *args, **options):
        try:
            result = sync_instagram_media()
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"{result['synced']} publicaciones sincronizadas "
                f"({result['created']} nuevas, {result['updated']} actualizadas)."
            )
        )
