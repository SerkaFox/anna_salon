from datetime import date, time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import User
from bookings.models import Booking
from clients.models import Client
from employees.models import Employee, EmployeeWeeklyShift
from salon.models import Zone
from services_app.models import Service


DEMO_SERVICES = [
    {
        "name": "Manicura básica",
        "category": Service.Categories.MANICURE,
        "duration_minutes": 45,
        "price": Decimal("25.00"),
        "color": "#F48FB1",
        "requires_zone": False,
    },
    {
        "name": "Manicura semipermanente",
        "category": Service.Categories.MANICURE,
        "duration_minutes": 60,
        "price": Decimal("35.00"),
        "color": "#CE93D8",
        "requires_zone": False,
    },
    {
        "name": "Pedicura completa",
        "category": Service.Categories.PEDICURE,
        "duration_minutes": 75,
        "price": Decimal("40.00"),
        "color": "#80DEEA",
        "requires_zone": True,
    },
    {
        "name": "Diseño de cejas",
        "category": Service.Categories.BROWS,
        "duration_minutes": 30,
        "price": Decimal("20.00"),
        "color": "#A5D6A7",
        "requires_zone": False,
    },
    {
        "name": "Lifting de pestañas",
        "category": Service.Categories.LASHES,
        "duration_minutes": 90,
        "price": Decimal("55.00"),
        "color": "#FFCC80",
        "requires_zone": False,
    },
    {
        "name": "Facial básico",
        "category": Service.Categories.FACIAL,
        "duration_minutes": 60,
        "price": Decimal("45.00"),
        "color": "#EF9A9A",
        "requires_zone": True,
    },
]

DEMO_EMPLOYEES = [
    {
        "first_name": "Ana",
        "last_name": "García",
        "email": "ana@salon-demo.es",
        "commission_percent": Decimal("40.00"),
        "categories": [Service.Categories.MANICURE, Service.Categories.PEDICURE, Service.Categories.BROWS],
        "weekdays": [0, 1, 2, 3, 4],
        "start_time": time(9, 0),
        "end_time": time(18, 0),
    },
    {
        "first_name": "Sofía",
        "last_name": "Martínez",
        "email": "sofia@salon-demo.es",
        "commission_percent": Decimal("40.00"),
        "categories": [Service.Categories.LASHES, Service.Categories.FACIAL],
        "weekdays": [1, 2, 3, 4, 5],
        "start_time": time(10, 0),
        "end_time": time(19, 0),
    },
]


class Command(BaseCommand):
    help = "Seed the database with demo salon data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--if-empty",
            action="store_true",
            help="Only seed if no services exist yet",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete all demo data before seeding (keeps superusers)",
        )

    def handle(self, *args, **options):
        if options["if_empty"] and Service.objects.exists():
            self.stdout.write("Database already seeded, skipping.")
            return

        if options["reset"]:
            self._reset()

        self.stdout.write("Seeding demo data...")

        zone = self._create_zone()
        services = self._create_services(zone)
        employees = self._create_employees(services)
        admin_user = self._create_admin()
        client_user, client = self._create_demo_client()
        self._create_bookings(client, employees, services)

        self.stdout.write(self.style.SUCCESS(
            "\n✓ Demo ready!\n"
            f"  Admin:  demo_admin / Demo2026!\n"
            f"  Client: demo_cliente / Demo2026!\n"
            f"  URL:    /panel/ (staff) or /clientes/portal/ (client)\n"
        ))

    def _reset(self):
        Booking.objects.all().delete()
        Client.objects.filter(user__username__startswith="demo_").delete()
        User.objects.filter(username__startswith="demo_").delete()
        EmployeeWeeklyShift.objects.all().delete()
        Employee.objects.all().delete()
        Service.objects.all().delete()
        Zone.objects.all().delete()
        self.stdout.write("Demo data reset.")

    def _create_zone(self):
        zone, _ = Zone.objects.get_or_create(
            name="Cabina 1",
            defaults={"zone_type": Zone.ZoneTypes.CABIN, "notes": "Cabina principal para tratamientos"},
        )
        Zone.objects.get_or_create(
            name="Mesa de manicura",
            defaults={"zone_type": Zone.ZoneTypes.TABLE, "notes": "Mesa de manicura y pedicura"},
        )
        return zone

    def _create_services(self, zone):
        services = []
        for data in DEMO_SERVICES:
            requires_zone = data.pop("requires_zone")
            svc, _ = Service.objects.get_or_create(
                name=data["name"],
                defaults={**data, "is_active": True},
            )
            if requires_zone:
                svc.allowed_zones.set(Zone.objects.all())
            services.append(svc)
            data["requires_zone"] = requires_zone
        return services

    def _create_employees(self, services):
        employees = []
        for data in DEMO_EMPLOYEES:
            categories = data.pop("categories")
            weekdays = data.pop("weekdays")
            start_time = data.pop("start_time")
            end_time = data.pop("end_time")

            emp, _ = Employee.objects.get_or_create(
                first_name=data["first_name"],
                last_name=data["last_name"],
                defaults={**data, "is_active": True},
            )

            matching_services = [s for s in services if s.category in categories]
            emp.services.set(matching_services)

            for weekday in weekdays:
                EmployeeWeeklyShift.objects.get_or_create(
                    employee=emp,
                    weekday=weekday,
                    defaults={
                        "is_day_off": False,
                        "start_time": start_time,
                        "end_time": end_time,
                    },
                )

            employees.append(emp)
            data["categories"] = categories
            data["weekdays"] = weekdays
            data["start_time"] = start_time
            data["end_time"] = end_time

        return employees

    def _create_admin(self):
        user, created = User.objects.get_or_create(
            username="demo_admin",
            defaults={
                "first_name": "Admin",
                "last_name": "Demo",
                "email": "admin@salon-demo.es",
                "role": User.ROLE_ADMIN,
                "is_staff": True,
            },
        )
        if created:
            user.set_password("Demo2026!")
            user.save()
        return user

    def _create_demo_client(self):
        user, created = User.objects.get_or_create(
            username="demo_cliente",
            defaults={
                "first_name": "María",
                "last_name": "López",
                "email": "cliente@salon-demo.es",
                "phone": "+34 600 123 456",
                "role": User.ROLE_CLIENT,
            },
        )
        if created:
            user.set_password("Demo2026!")
            user.save()

        client, _ = Client.objects.get_or_create(
            user=user,
            defaults={
                "first_name": "María",
                "last_name": "López",
                "phone": "+34 600 123 456",
                "email": "cliente@salon-demo.es",
            },
        )
        return user, client

    def _create_bookings(self, client, employees, services):
        today = timezone.localdate()
        manicura = next((s for s in services if s.category == Service.Categories.MANICURE), services[0])
        pedicura = next((s for s in services if s.category == Service.Categories.PEDICURE), services[0])
        lashes = next((s for s in services if s.category == Service.Categories.LASHES), services[0])
        ana = employees[0]
        sofia = employees[1] if len(employees) > 1 else employees[0]

        # Past completed booking
        past_date = today - timedelta(days=14)
        past_start = timezone.make_aware(
            timezone.datetime.combine(past_date, time(11, 0))
        )
        Booking.objects.get_or_create(
            client=client,
            employee=ana,
            service=manicura,
            start_at=past_start,
            defaults={
                "end_at": past_start + timedelta(minutes=manicura.duration_minutes),
                "status": Booking.Statuses.CONFIRMED,
                "source": Booking.Sources.WEBSITE,
                "price_snapshot": manicura.price,
                "client_price_snapshot": manicura.price,
                "employee_amount_snapshot": manicura.price * (ana.commission_percent / 100),
            },
        )

        # Another past booking
        past_date2 = today - timedelta(days=7)
        past_start2 = timezone.make_aware(
            timezone.datetime.combine(past_date2, time(10, 0))
        )
        Booking.objects.get_or_create(
            client=client,
            employee=sofia,
            service=lashes,
            start_at=past_start2,
            defaults={
                "end_at": past_start2 + timedelta(minutes=lashes.duration_minutes),
                "status": Booking.Statuses.CONFIRMED,
                "source": Booking.Sources.WEBSITE,
                "price_snapshot": lashes.price,
                "client_price_snapshot": lashes.price,
                "employee_amount_snapshot": lashes.price * (sofia.commission_percent / 100),
            },
        )

        # Upcoming booking — next available weekday
        future_date = today + timedelta(days=3)
        while future_date.weekday() >= 5:
            future_date += timedelta(days=1)
        future_start = timezone.make_aware(
            timezone.datetime.combine(future_date, time(12, 0))
        )
        Booking.objects.get_or_create(
            client=client,
            employee=ana,
            service=pedicura,
            start_at=future_start,
            defaults={
                "end_at": future_start + timedelta(minutes=pedicura.duration_minutes),
                "status": Booking.Statuses.CONFIRMED,
                "source": Booking.Sources.WEBSITE,
                "price_snapshot": pedicura.price,
                "client_price_snapshot": pedicura.price,
                "employee_amount_snapshot": pedicura.price * (ana.commission_percent / 100),
            },
        )
