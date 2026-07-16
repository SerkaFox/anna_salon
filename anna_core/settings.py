
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_dotenv(BASE_DIR / ".env")

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {name} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "file": {
            "level": "ERROR",
            "class": "logging.FileHandler",
            "filename": LOG_DIR / "django-error.log",
            "formatter": "verbose",
        },
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": True,
        },
        "django.request": {
            "handlers": ["console", "file"],
            "level": "ERROR",
            "propagate": False,
        },
        "django.server": {
            "handlers": ["console", "file"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'brimoon-dev-temporary-secret-key-change-me')
DEBUG = False

ALLOWED_HOSTS = [
    'brimoon.es',
    'www.brimoon.es',
    '127.0.0.1',
    'localhost',
    '192.168.0.159',
]
_extra_hosts = [h.strip() for h in os.getenv('EXTRA_ALLOWED_HOSTS', '').split(',') if h.strip()]
if _extra_hosts:
    ALLOWED_HOSTS += _extra_hosts

CSRF_TRUSTED_ORIGINS = [
    'https://brimoon.es',
    'https://www.brimoon.es',
]
_extra_origins = [o.strip() for o in os.getenv('EXTRA_CSRF_ORIGINS', '').split(',') if o.strip()]
if _extra_origins:
    CSRF_TRUSTED_ORIGINS += _extra_origins

SITE_URL = os.getenv('SITE_URL', 'https://brimoon.es')
PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL', SITE_URL)
APP_DOWNLOAD_URL = os.getenv('APP_DOWNLOAD_URL', 'https://brimoon.es/app/')
META_APP_ID = os.getenv('META_APP_ID', '')
META_APP_SECRET = os.getenv('META_APP_SECRET', '')
INSTAGRAM_ACCESS_TOKEN = os.getenv('INSTAGRAM_ACCESS_TOKEN', '')
INSTAGRAM_ACCOUNT_ID = os.getenv('INSTAGRAM_ACCOUNT_ID', '17841425950738982')
INSTAGRAM_WEBHOOK_VERIFY_TOKEN = os.getenv('INSTAGRAM_WEBHOOK_VERIFY_TOKEN', 'brimoon_instagram_verify_2026')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'salon', # — зоны, кабинеты, ресурсы
    'services_app', #— услуги и цены
    'bookings', #  — записи и календарь 
    'accounts', # — вход, роли, пользователи
    'dashboard', # — главная внутренняя панель
    'clients', # — база клиентов
    'employees', # — сотрудники
    'documents', # — recibos, facturas y exportación fiscal
    'payments', # — TPV virtual / Redsys
    'gallery', # — Instagram gallery
    'auditlog', # — journal de cambios
    'core',
    'mobile_api',
    'whatsapp_bot',
    'reviews',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'anna_core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'anna_core.context_processors.salon_globals',
            ],
        },
    },
]

WSGI_APPLICATION = 'anna_core.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': os.getenv('DB_ENGINE', 'django.db.backends.postgresql'),
        'NAME': os.getenv('DB_NAME', 'anna_db'),
        'USER': os.getenv('DB_USER', 'anna_user'),
        'PASSWORD': os.getenv('DB_PASSWORD', ''),
        'HOST': os.getenv('DB_HOST', '127.0.0.1'),
        'PORT': os.getenv('DB_PORT', '5432'),
        'CONN_MAX_AGE': int(os.getenv('DB_CONN_MAX_AGE', '60')),
    }
}

LANGUAGE_CODE = 'es'
TIME_ZONE = 'Europe/Madrid'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = os.getenv('STATIC_ROOT', '/var/www/anna/static')

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True

AUTH_USER_MODEL = 'accounts.User'

LOGIN_URL = 'accounts:login'
LOGIN_REDIRECT_URL = 'dashboard:home'
LOGOUT_REDIRECT_URL = 'accounts:login'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework.authentication.BasicAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}

REDSYS_MERCHANT_CODE = os.getenv('REDSYS_MERCHANT_CODE', '999008881')
REDSYS_TERMINAL = os.getenv('REDSYS_TERMINAL', '001')
REDSYS_SECRET_KEY = os.getenv('REDSYS_SECRET_KEY', '')
REDSYS_ENVIRONMENT = os.getenv('REDSYS_ENVIRONMENT', 'test')
REDSYS_CURRENCY = os.getenv('REDSYS_CURRENCY', '978')
REDSYS_TRANSACTION_TYPE = os.getenv('REDSYS_TRANSACTION_TYPE', '0')

STRIPE_PUBLIC_KEY = os.getenv('STRIPE_PUBLIC_KEY', '')
STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET', '')
STRIPE_CURRENCY = os.getenv('STRIPE_CURRENCY', 'eur').lower()
BOOKING_DEPOSIT_AMOUNT_EUR = os.getenv('BOOKING_DEPOSIT_AMOUNT_EUR', '')
BOOKING_FREE_CANCEL_HOURS = int(os.getenv('BOOKING_FREE_CANCEL_HOURS', '24'))
BOOKING_DEPOSIT_PERCENT = os.getenv('BOOKING_DEPOSIT_PERCENT', '10')

WHATSAPP_BRIDGE_URL = os.getenv('WHATSAPP_BRIDGE_URL', '')
WHATSAPP_BRIDGE_TOKEN = os.getenv('WHATSAPP_BRIDGE_TOKEN', '')
WHATSAPP_DRY_RUN = os.getenv('WHATSAPP_DRY_RUN', 'true').lower() in {'1', 'true', 'yes', 'on'}
WHATSAPP_SEND_CONFIRMATION_ON_BOOKING = os.getenv('WHATSAPP_SEND_CONFIRMATION_ON_BOOKING', 'true').lower() in {'1', 'true', 'yes', 'on'}
WHATSAPP_DEFAULT_COUNTRY_CODE = os.getenv('WHATSAPP_DEFAULT_COUNTRY_CODE', '34')
WHATSAPP_LOCAL_PHONE_LENGTH = int(os.getenv('WHATSAPP_LOCAL_PHONE_LENGTH', '9'))
WHATSAPP_CONNECTION_NAME = os.getenv('WHATSAPP_CONNECTION_NAME', 'main')

DEMO_MODE = os.getenv('DEMO_MODE', 'false').lower() in {'1', 'true', 'yes', 'on'}
SALON_NAME = os.getenv('SALON_NAME', 'BRIMOON Studio')

GOOGLE_PLACES_API_KEY = os.getenv('GOOGLE_PLACES_API_KEY', '')
GOOGLE_PLACE_ID = os.getenv('GOOGLE_PLACE_ID', '')
GOOGLE_PLACE_REVIEW_COUNT = int(os.getenv('GOOGLE_PLACE_REVIEW_COUNT', '91'))
