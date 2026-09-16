#!/usr/bin/env python3
"""Read-only BRIMOON probes; reuse Pushik credentials without copying secrets."""
import concurrent.futures
import datetime
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path('/home/seradmin/anna')
STATE = ROOT / 'logs/brimoon_watchdog.json'
BOT = Path('/home/seradmin/pushik-sergey')


def env_file(path):
    result = {}
    for line in path.read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            result[key.strip()] = value.strip().strip('\"\'')
    return result


def fetch(url, headers=None, as_json=True):
    headers = dict(headers or {}, **{'Cache-Control': 'no-cache', 'User-Agent': 'Mozilla/5.0 (compatible; BRIMOON-HealthMonitor/1.0)'})
    with urlopen(Request(url, headers=headers), timeout=8) as response:
        body = response.read(262144)
        if response.status != 200:
            raise ValueError('HTTP ' + str(response.status))
        return json.loads(body) if as_json else body.decode('utf-8')


def api(url, headers=None):
    data = fetch(url, headers)
    if data.get('status') != 'ok' or data.get('database') is not True:
        raise ValueError('API/database not ready')
    return 'API и база данных отвечают'


def website():
    page = fetch('https://brimoon.es/?watchdog=' + str(int(time.time())), as_json=False)
    if '<html' not in page.lower() or 'brimoon' not in page.lower():
        raise ValueError('Unexpected website response')
    return 'Сайт отвечает HTTPS'


def app_update():
    data = fetch('https://brimoon.es/api/v1/app-update/?source=android&watchdog=' + str(int(time.time())))
    if not isinstance(data, dict) or not data:
        raise ValueError('Invalid application update manifest')
    return 'API обновлений приложения отвечает JSON'


def whatsapp(config):
    base = config['WHATSAPP_BRIDGE_URL'].rstrip('/')
    headers = {'Authorization': 'Bearer ' + config.get('WHATSAPP_BRIDGE_TOKEN', '')}
    if fetch(base + '/health', headers).get('ok') is not True:
        raise ValueError('Bridge health failed')
    data = fetch(base + '/sessions/main/status', headers)
    status = data.get('status')
    if status != 'ready':
        if status in ('starting', 'authenticated') and time.time() - data.get('status_since', 0)/1000 < 120:
            return 'Запускается (льготные 120 секунд)'
        reason = 'требуется вход по QR: https://brimoon.es/whatsapp/connect/main/' if status == 'qr' else 'мост не готов'
        raise ValueError(str(status) + ' — ' + reason)
    actual = fetch(base + '/sessions/main/state', headers)
    if actual.get('wa_state') != 'CONNECTED':
        raise ValueError('Browser WhatsApp not CONNECTED')
    return 'WhatsApp CONNECTED'


def services():
    names = ['anna.service', 'brimoon-whatsapp-bridge.service', 'nginx.service']
    failed = [name for name in names if subprocess.run(['systemctl', 'is-active', '--quiet', name], timeout=5).returncode]
    if failed:
        raise ValueError('Неактивны: ' + ', '.join(failed))
    return 'Службы запущены'


def resources():
    disk = shutil.disk_usage(ROOT)
    if disk.free < 2 * 1024**3 or disk.free / disk.total < .05:
        raise ValueError('Мало места на диске: %.1f GB' % (disk.free / 1024**3))
    mem = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    if int(mem['MemAvailable'].split()[0]) < 300 * 1024:
        raise ValueError('Свободная RAM меньше 300 MB')
    carrier = Path('/sys/class/net/eno1/carrier')
    if carrier.exists() and carrier.read_text().strip() != '1':
        raise ValueError('eno1: нет подключения кабеля')
    return 'Диск, память и сетевой интерфейс в норме'


def notify(message):
    token = env_file(BOT / 'telegram_bridge.env')['TG_BOT_TOKEN']
    chats = list(json.loads((BOT / 'bridge_state.json').read_text()).get('sessions', {}))
    if not chats:
        raise ValueError('Pushik has no configured chat')
    payload = json.dumps({'chat_id': chats[0], 'text': message, 'disable_web_page_preview': True}).encode()
    request = Request('https://api.telegram.org/bot' + token + '/sendMessage', data=payload,
                      headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=10) as response:
        if not json.load(response).get('ok'):
            raise ValueError('Telegram rejected notification')


def transition(previous, result, now):
    """Two failed samples, hourly reminders, recovery only after delivered alert."""
    item = dict(previous)
    ok, detail = result
    item['failures'] = 0 if ok else item.get('failures', 0) + 1
    item['detail'] = detail
    message = None
    if ok and item.get('alerted'):
        message = '✅ Восстановлено: ' + detail
    elif not ok and item['failures'] >= 2 and (not item.get('alerted') or now - item.get('sent', 0) >= 3600):
        message = '❌ Ошибка: ' + detail
    return item, message


def probe(func):
    try:
        return True, func()
    except HTTPError as error:
        return False, 'HTTP ' + str(error.code)
    except Exception as error:
        # No exception URLs or credentials in logs/messages.
        return False, str(error)[:240] if isinstance(error, ValueError) else type(error).__name__


def main():
    if '--test' in sys.argv:
        notify('🧪 BRIMOON: тест Пушика. Монитор сайта, API, WhatsApp и ресурсов сервера установлен.')
        print('Telegram test delivered')
        return
    STATE.parent.mkdir(exist_ok=True)
    with open(str(STATE) + '.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            state = json.loads(STATE.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            state = {}
        config = env_file(ROOT / '.env')
        nonce = '?watchdog=' + str(int(time.time()))
        checks = {'Сайт': website, 'API публичный': lambda: api('https://brimoon.es/api/v1/health/' + nonce),
                  'API сервер': lambda: api('http://127.0.0.1:8010/api/v1/health/' + nonce, {'Host': 'brimoon.es', 'X-Forwarded-Proto': 'https'}),
                  'Обновления приложения': app_update,
                  'WhatsApp': lambda: whatsapp(config), 'Службы': services, 'Ресурсы': resources}
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = dict(zip(checks, pool.map(probe, checks.values())))
        now = time.time()
        stamp = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
        for name, result in results.items():
            item, message = transition(state.get(name, {}), result, now)
            if message:
                try:
                    notify('BRIMOON — ' + name + '\n' + message + '\n' + stamp)
                    item['alerted'] = not result[0]
                    item['sent'] = now
                except Exception as error:
                    print('Telegram delivery failed: ' + type(error).__name__)
            state[name] = item
            print(name + ': ' + ('OK' if result[0] else 'FAIL') + ' — ' + result[1])
        temporary = STATE.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=False))
        os.chmod(temporary, 0o600)
        temporary.replace(STATE)


if __name__ == '__main__':
    main()
