# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

BRIMOON Studio — a Django 6 backend for a beauty salon: clients, employees, services, zones, bookings/calendar, payments, an internal staff dashboard, and a REST API consumed by a separate mobile app. Production domain is `brimoon.es`, deployed as gunicorn behind nginx (see `DEPLOY_BRIMOON.md`).

Stack: Python 3.12, Django 6, PostgreSQL, Django REST Framework, Stripe + Redsys for payments.

## Commands

```bash
# Activate the existing venv (already created at venv/) rather than creating a new one
source venv/bin/activate

# Run the dev server
python manage.py runserver

# Migrations
python manage.py makemigrations
python manage.py migrate

# Tests (stdlib unittest via Django's test runner — no pytest)
python manage.py test                                   # whole suite
python manage.py test mobile_api bookings clients employees accounts  # app subset, run before pushing backend changes
python manage.py test bookings.tests.BookingModelTest.test_x          # single test

# Sanity check before deploy
python manage.py check
python manage.py collectstatic --noinput
```

There is no linter/formatter configured in this repo (no flake8/black/ruff config) — match existing style rather than introducing one.

## Architecture

### App layout and request flow

`anna_core/urls.py` is the single root URLConf and shows the whole site shape at a glance:
- `/` and a handful of public marketing/booking paths are wired directly to `core.views` (home, service pages, advice articles, public booking flow, sitemap/robots) and `gallery.views` (public Instagram-backed gallery).
- `/api/v1/` → `mobile_api` — a DRF API that mirrors most of the backend's domain logic for the mobile app.
- `/payments/` → `payments` — Redsys and Stripe webhook/return endpoints, outside `/panel/`.
- `/panel/...` → the internal staff dashboard, one include per domain app: `dashboard`, `clients`, `employees`, `services_app` (`/panel/servicios/`), `salon` (`/panel/zonas/`), `bookings` (`/panel/reservas/`), `documents`, `gallery`, `auditlog`.
- `/dj-admin/` is the Django admin, kept separate from `/panel/` (the custom staff dashboard).

So most domain logic gets implemented twice at the view layer: once for the server-rendered `/panel/` dashboard (`<app>/views.py` + `templates/<app>/`) and once for `/api/v1/` (`mobile_api/views.py`, a single ~1450-line file with one DRF view class per resource). `BACKEND_MOBILE_PARITY.md` tracks where the web panel still lags behind what the mobile API/app already supports — check it before assuming a feature only needs to exist in one place.

### Roles and access scoping

`accounts.User` (`AUTH_USER_MODEL`) has a `role` field: `owner`, `admin`, `employee`, `client`. A `User` optionally links to an `employees.Employee` (`employee_profile`) or `clients.Client` (`client_profile`) via `OneToOneField`.

`accounts/permissions.py` is the authorization core for the `/panel/` views: helpers like `is_admin_user`, `scope_bookings_queryset`, `scope_clients_queryset`, `can_access_booking`, `can_access_client` filter querysets/objects based on the requesting user's role (admins see everything, employees see their own bookings/clients, clients see only themselves). `mobile_api/views.py` reimplements an equivalent set of scoping helpers locally (`_mobile_employees_queryset`, `_mobile_bookings_queryset`, `_mobile_can_access_booking`, etc.) rather than importing from `accounts.permissions` — when changing access rules, both places usually need updating.

### Booking domain

`bookings.Booking` is the central model, FK'd to `Client`, `Employee`, `Service`, and optionally `Zone`. It snapshots price/duration/commission at creation time (`price_snapshot`, `client_price_snapshot`, `employee_amount_snapshot`, etc.) so later edits to `Service`/`Employee` don't retroactively change historical bookings.

Availability and scheduling logic lives in `bookings/utils.py`, not on the model:
- `Employee.get_shift_for_date()` resolves weekly shifts vs one-off `EmployeeScheduleOverride`/time blocks (`employees/models.py`).
- `is_slot_available`, `find_available_zone`, and `build_available_slots_for_day` are the slot-conflict checks shared by the public booking form, the client portal, the staff calendar, and the mobile API — reuse these rather than re-deriving availability rules.
- `Service.requires_zone` controls whether a booking needs a `salon.Zone` (cabin/table/etc.) in addition to an employee; zone conflicts are checked separately from employee conflicts.

`core/booking_requests.py` handles the public (unauthenticated) booking form's server-side validation and booking creation, reusing `bookings.forms.BookingForm` and `bookings.utils.find_available_zone`.

### Payments

Two independent payment providers, both modeled through `payments.Payment` (`provider` is `redsys` or `stripe`):
- `payments/redsys.py` — Redsys TPV virtual integration (Spanish card gateway), notification/return endpoints under `/payments/redsys/...`.
- `payments/stripe_service.py` — Stripe Checkout sessions + webhook handling, endpoints under `/payments/stripe/...`.

`bookings.BookingPrepayment` links a `Booking` to a `Payment` for deposit/prepayment flows; `bookings/services.py` has the prepayment math (`PREPAYMENT_PERCENT`, refund-eligibility window via `BOOKING_FREE_CANCEL_HOURS`) and waitlist notification (`notify_waitlist_for_booking_opening`, emails clients on `BookingWaitlistEntry` when a matching slot frees up).

### Client rewards

`clients.ClientRewardRule` defines configurable reward thresholds (referrals / visits / amount spent). `clients/rewards.py::client_reward_progress(client)` is the single source of truth for computing a client's progress/availability per rule — both the web client portal and the mobile API call into it rather than recomputing reward math themselves.

### i18n: not Django gettext

Despite `USE_I18N = True`, translated public-facing copy does **not** use Django's `.po`/`gettext` machinery. Instead `core/i18n.py` and `clients/translation.py` define plain Python dicts (`PUBLIC_TRANSLATIONS`, `CLIENT_TRANSLATIONS`, `SERVICE_TRANSLATIONS`, `ARTICLE_TRANSLATIONS`) keyed by language code (`es`/`ru`/`en`/`de`/`fr`) and by content slug. Adding/editing public copy means editing these dicts directly (and updating all 5 languages), not running `makemessages`. Selected language is stored in the session (`PUBLIC_LANGUAGE_SESSION_KEY` / `CLIENT_LANGUAGE_SESSION_KEY`), not via `LocaleMiddleware`.

### Auditing

`auditlog.services.log_event(actor=, section=, action=, message=, instance=, metadata=)` writes an `AuditEvent` row capturing who did what to which object. Call this from `/panel/` views when mutating sensitive records (clients, bookings, payments) — check existing call sites in `clients/views.py`/`bookings/views.py` for the established `section`/`action` vocabulary before inventing new values.

### Settings/env

`anna_core/settings.py` loads `.env` itself via a small hand-rolled `load_dotenv()` (not `django-environ`/`python-dotenv`) — it only sets values that aren't already in the environment (`setdefault`), and values are otherwise read with `os.getenv(...)` with hardcoded production-safe defaults. `DEBUG = False` is hardcoded (no env override). Required vars are listed in `.env.example` and `README.md`.
