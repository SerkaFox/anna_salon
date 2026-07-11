#!/bin/bash
set -e

echo "[demo] Waiting for PostgreSQL..."
until python -c "
import os, sys
import psycopg
try:
    psycopg.connect(
        host=os.environ.get('DB_HOST', 'db'),
        port=os.environ.get('DB_PORT', '5432'),
        dbname=os.environ.get('DB_NAME', 'salon_demo'),
        user=os.environ.get('DB_USER', 'salon_demo'),
        password=os.environ.get('DB_PASSWORD', ''),
    ).close()
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null; do
    sleep 1
done
echo "[demo] PostgreSQL ready."

python manage.py migrate --noinput
python manage.py collectstatic --noinput --clear

python manage.py seed_demo --if-empty

exec gunicorn anna_core.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 2 \
    --timeout 60 \
    --access-logfile - \
    --error-logfile -
