"""Refuse a deployment reload if WhatsApp is connecting or already ready."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'anna_core.settings')
import django
django.setup()
from whatsapp_bot.bridge import get_status
from whatsapp_bot.models import WhatsAppConnection

state = get_status(WhatsAppConnection.objects.get(name='main'))
print('Pre-reload WhatsApp status:', state.get('status'))
if state.get('status') in ('ready', 'authenticated') or state.get('pairing_busy'):
    raise SystemExit('Reload refused: WhatsApp connected or currently pairing')
