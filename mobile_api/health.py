import logging

from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@never_cache
@require_GET
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        logging.getLogger(__name__).exception("API database health check failed")
        return JsonResponse({"status": "error", "database": False}, status=503)
    return JsonResponse({"status": "ok", "database": True})
