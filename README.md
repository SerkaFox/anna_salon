# Anna Salon

Django application for salon management: clients, employees, services, zones, bookings, and the internal dashboard.

## Stack

- Python 3.12
- Django 6
- PostgreSQL

## Local setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy the environment template and fill in your secrets:

```bash
cp .env.example .env
```

4. Export environment variables from `.env` or set them manually.
5. Run migrations:

```bash
python manage.py migrate
```

6. Start the development server:

```bash
python manage.py runserver
```

## Required environment variables

- `DJANGO_SECRET_KEY`
- `DB_ENGINE`
- `DB_NAME`
- `DB_USER`
- `DB_PASSWORD`
- `DB_HOST`
- `DB_PORT`
- `DB_CONN_MAX_AGE`

## Project structure

- `anna_core/`: Django settings and root URLs
- `accounts/`: authentication and users
- `clients/`: client records
- `employees/`: employee management
- `services_app/`: services and pricing
- `salon/`: salon zones and resources
- `bookings/`: bookings, calendar, and availability
- `dashboard/`: internal dashboard
- `templates/` and `static/`: UI templates and styles
