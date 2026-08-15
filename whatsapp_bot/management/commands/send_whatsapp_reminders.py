from django.core.management.base import BaseCommand

from whatsapp_bot.services import (
    queue_due_reminders,
    queue_due_review_requests,
    process_unanswered_24h_reminders,
    process_expired_prepayment_requests,
    send_due_messages,
)


class Command(BaseCommand):
    help = "Queue and send WhatsApp booking reminders."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, choices=[2, 24], action="append")
        parser.add_argument("--window-minutes", type=int, default=15)
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--queue-only", action="store_true")
        parser.add_argument("--response-timeout-minutes", type=int, default=15)

    def handle(self, *args, **options):
        hours_values = options["hours"] or [24, 2]
        total_queued = 0
        total_skipped = 0
        for hours in hours_values:
            result = queue_due_reminders(hours=hours, window_minutes=options["window_minutes"])
            total_queued += len(result["queued"])
            total_skipped += len(result["skipped"])
        total_queued += len(queue_due_review_requests())
        timeout_result = process_unanswered_24h_reminders(
            timeout_minutes=options["response_timeout_minutes"]
        )
        prepayment_result = process_expired_prepayment_requests()
        sent = [] if options["queue_only"] else send_due_messages(limit=options["limit"])
        self.stdout.write(
            f"queued={total_queued} skipped_existing={total_skipped} "
            f"auto_cancelled={len(timeout_result['cancelled'])} "
            f"auto_cancel_failed={len(timeout_result['failed'])} processed={len(sent)}"
            f" prepayment_cancelled={len(prepayment_result['cancelled'])}"
            f" prepayment_failed={len(prepayment_result['failed'])}"
        )
