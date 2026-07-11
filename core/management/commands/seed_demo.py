import random
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
    {"name": "Manicura básica", "category": Service.Categories.MANICURE, "duration_minutes": 45, "price": Decimal("25.00"), "color": "#F48FB1", "requires_zone": False},
    {"name": "Manicura semipermanente", "category": Service.Categories.MANICURE, "duration_minutes": 60, "price": Decimal("35.00"), "color": "#CE93D8", "requires_zone": False},
    {"name": "Manicura con nail art", "category": Service.Categories.MANICURE, "duration_minutes": 90, "price": Decimal("50.00"), "color": "#F06292", "requires_zone": False},
    {"name": "Pedicura completa", "category": Service.Categories.PEDICURE, "duration_minutes": 75, "price": Decimal("40.00"), "color": "#80DEEA", "requires_zone": True},
    {"name": "Pedicura semipermanente", "category": Service.Categories.PEDICURE, "duration_minutes": 90, "price": Decimal("55.00"), "color": "#4DD0E1", "requires_zone": True},
    {"name": "Diseño de cejas", "category": Service.Categories.BROWS, "duration_minutes": 30, "price": Decimal("20.00"), "color": "#A5D6A7", "requires_zone": False},
    {"name": "Laminado de cejas", "category": Service.Categories.BROWS, "duration_minutes": 60, "price": Decimal("45.00"), "color": "#66BB6A", "requires_zone": False},
    {"name": "Lifting de pestañas", "category": Service.Categories.LASHES, "duration_minutes": 90, "price": Decimal("55.00"), "color": "#FFCC80", "requires_zone": False},
    {"name": "Extensiones de pestañas", "category": Service.Categories.LASHES, "duration_minutes": 120, "price": Decimal("75.00"), "color": "#FFA726", "requires_zone": False},
    {"name": "Facial hidratante", "category": Service.Categories.FACIAL, "duration_minutes": 60, "price": Decimal("45.00"), "color": "#EF9A9A", "requires_zone": True},
    {"name": "Depilación piernas completas", "category": Service.Categories.DEPILATION, "duration_minutes": 40, "price": Decimal("30.00"), "color": "#B39DDB", "requires_zone": True},
]

DEMO_EMPLOYEES = [
    {
        "first_name": "Ana", "last_name": "García",
        "email": "ana@salon-demo.es", "phone": "+34 612 345 678",
        "commission_percent": Decimal("40.00"),
        "categories": [Service.Categories.MANICURE, Service.Categories.PEDICURE],
        "weekdays": [0, 1, 2, 3, 4], "start_time": time(9, 0), "end_time": time(17, 0),
    },
    {
        "first_name": "Sofía", "last_name": "Martínez",
        "email": "sofia@salon-demo.es", "phone": "+34 623 456 789",
        "commission_percent": Decimal("40.00"),
        "categories": [Service.Categories.LASHES, Service.Categories.BROWS],
        "weekdays": [1, 2, 3, 4, 5], "start_time": time(10, 0), "end_time": time(18, 0),
    },
    {
        "first_name": "Carmen", "last_name": "López",
        "email": "carmen@salon-demo.es", "phone": "+34 634 567 890",
        "commission_percent": Decimal("35.00"),
        "categories": [Service.Categories.FACIAL, Service.Categories.DEPILATION, Service.Categories.PEDICURE],
        "weekdays": [0, 2, 3, 4, 5], "start_time": time(9, 0), "end_time": time(18, 0),
    },
]

DEMO_CLIENTS = [
    {"first_name": "María", "last_name": "López", "phone": "+34 600 123 456", "email": "maria@example.com"},
    {"first_name": "Laura", "last_name": "García", "phone": "+34 611 234 567", "email": "laura@example.com"},
    {"first_name": "Elena", "last_name": "Martínez", "phone": "+34 622 345 678", "email": "elena@example.com"},
    {"first_name": "Natalia", "last_name": "Fernández", "phone": "+34 633 456 789", "email": "natalia@example.com"},
    {"first_name": "Irina", "last_name": "Volkova", "phone": "+34 644 567 890", "email": "irina@example.com"},
    {"first_name": "Ana", "last_name": "Ruiz", "phone": "+34 655 678 901", "email": "ana.r@example.com"},
    {"first_name": "Marta", "last_name": "Sánchez", "phone": "+34 666 789 012", "email": "marta@example.com"},
    {"first_name": "Sara", "last_name": "Jiménez", "phone": "+34 677 890 123", "email": "sara@example.com"},
    {"first_name": "Lucía", "last_name": "Moreno", "phone": "+34 688 901 234", "email": "lucia@example.com"},
    {"first_name": "Paula", "last_name": "Álvarez", "phone": "+34 699 012 345", "email": "paula@example.com"},
]


class Command(BaseCommand):
    help = "Seed the database with demo salon data"

    def add_arguments(self, parser):
        parser.add_argument("--if-empty", action="store_true", help="Only seed if no services exist yet")
        parser.add_argument("--reset", action="store_true", help="Delete all demo data before seeding")

    def handle(self, *args, **options):
        if options["if_empty"] and Service.objects.exists():
            self.stdout.write("Database already seeded, skipping.")
            return

        if options["reset"]:
            self._reset()

        self.stdout.write("Seeding demo data...")

        random.seed(42)

        zones = self._create_zones()
        services = self._create_services(zones)
        employees = self._create_employees(services)
        self._create_admin()
        demo_client_user, demo_client = self._create_demo_client()
        extra_clients = self._create_extra_clients()
        all_clients = [demo_client] + extra_clients
        self._create_bookings(all_clients, employees, services)

        self.stdout.write(self.style.SUCCESS(
            "\n✓ Demo listo!\n"
            f"  Admin:   demo_admin / Demo2026!   →  /panel/\n"
            f"  Cliente: demo_cliente / Demo2026! →  /clientes/portal/\n"
        ))

    def _reset(self):
        Booking.objects.all().delete()
        Client.objects.filter(user__username__startswith="demo_").delete()
        User.objects.filter(username__startswith="demo_").delete()
        EmployeeWeeklyShift.objects.all().delete()
        Employee.objects.all().delete()
        Service.objects.all().delete()
        Zone.objects.all().delete()
        Client.objects.filter(email__endswith="@example.com").delete()
        self.stdout.write("Demo data reset.")

    def _create_zones(self):
        zones = []
        for name, zone_type in [
            ("Cabina 1", Zone.ZoneTypes.CABIN),
            ("Cabina 2", Zone.ZoneTypes.CABIN),
            ("Mesa de manicura 1", Zone.ZoneTypes.TABLE),
            ("Mesa de manicura 2", Zone.ZoneTypes.TABLE),
        ]:
            z, _ = Zone.objects.get_or_create(name=name, defaults={"zone_type": zone_type})
            zones.append(z)
        return zones

    def _create_services(self, zones):
        cabin_zones = [z for z in zones if z.zone_type == Zone.ZoneTypes.CABIN]
        table_zones = [z for z in zones if z.zone_type == Zone.ZoneTypes.TABLE]
        services = []
        for data in DEMO_SERVICES:
            requires_zone = data.pop("requires_zone")
            svc, _ = Service.objects.get_or_create(name=data["name"], defaults={**data, "is_active": True})
            if requires_zone:
                cat = data.get("category")
                if cat == Service.Categories.PEDICURE:
                    svc.allowed_zones.set(table_zones)
                else:
                    svc.allowed_zones.set(cabin_zones)
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
                first_name=data["first_name"], last_name=data["last_name"],
                defaults={**data, "is_active": True},
            )
            matching = [s for s in services if s.category in categories]
            emp.services.set(matching)
            for weekday in weekdays:
                EmployeeWeeklyShift.objects.get_or_create(
                    employee=emp, weekday=weekday,
                    defaults={"is_day_off": False, "start_time": start_time, "end_time": end_time},
                )
            employees.append(emp)
            data.update({"categories": categories, "weekdays": weekdays, "start_time": start_time, "end_time": end_time})
        return employees

    def _create_admin(self):
        user, created = User.objects.get_or_create(
            username="demo_admin",
            defaults={"first_name": "Admin", "last_name": "Demo", "email": "admin@salon-demo.es", "role": User.ROLE_ADMIN, "is_staff": True},
        )
        if created:
            user.set_password("Demo2026!")
            user.save()
        return user

    def _create_demo_client(self):
        user, created = User.objects.get_or_create(
            username="demo_cliente",
            defaults={"first_name": "María", "last_name": "López", "email": "maria@example.com", "phone": "+34 600 123 456", "role": User.ROLE_CLIENT},
        )
        if created:
            user.set_password("Demo2026!")
            user.save()
        client, _ = Client.objects.get_or_create(
            user=user,
            defaults={"first_name": "María", "last_name": "López", "phone": "+34 600 123 456", "email": "maria@example.com"},
        )
        return user, client

    def _create_extra_clients(self):
        clients = []
        for i, data in enumerate(DEMO_CLIENTS[1:], start=2):
            client, _ = Client.objects.get_or_create(
                email=data["email"],
                defaults=data,
            )
            clients.append(client)
        return clients

    def _create_bookings(self, clients, employees, services):
        today = timezone.localdate()

        def make_booking(client, employee, service, target_date, hour, minute=0):
            start = timezone.make_aware(timezone.datetime.combine(target_date, time(hour, minute)))
            end = start + timedelta(minutes=service.duration_minutes)
            if Booking.objects.filter(employee=employee, start_at=start).exists():
                return
            price = service.price
            commission = price * (employee.commission_percent / 100)
            is_past = target_date < today
            Booking.objects.create(
                client=client, employee=employee, service=service,
                start_at=start, end_at=end,
                status=Booking.Statuses.CONFIRMED,
                source=Booking.Sources.WEBSITE if is_past else Booking.Sources.MANUAL,
                price_snapshot=price, client_price_snapshot=price,
                employee_amount_snapshot=commission,
            )

        ana, sofia, carmen = employees[0], employees[1], employees[2]

        # Services per employee (categories they handle)
        emp_services = {
            ana.pk:    [s for s in services if s.category in (Service.Categories.MANICURE, Service.Categories.PEDICURE)],
            sofia.pk:  [s for s in services if s.category in (Service.Categories.LASHES, Service.Categories.BROWS)],
            carmen.pk: [s for s in services if s.category in (Service.Categories.FACIAL, Service.Categories.DEPILATION, Service.Categories.PEDICURE)],
        }

        # Time slots per employee (fixed slots that don't overlap even for 120-min services)
        emp_slots = {
            ana.pk:    [(9, 0), (11, 0), (13, 30), (15, 30)],   # works 9-17
            sofia.pk:  [(10, 0), (12, 0), (14, 30), (16, 30)],  # works 10-18/19
            carmen.pk: [(9, 0), (11, 30), (14, 0), (16, 0)],    # works 9-18
        }

        # Working weekdays per employee (0=Mon)
        emp_weekdays = {
            ana.pk:    {0, 1, 2, 3, 4},
            sofia.pk:  {1, 2, 3, 4, 5},
            carmen.pk: {0, 2, 3, 4, 5},
        }

        # Generate bookings from -90 days to +365 days
        for day_offset in range(-90, 366):
            target_date = today + timedelta(days=day_offset)
            weekday = target_date.weekday()

            for emp in employees:
                if weekday not in emp_weekdays[emp.pk]:
                    continue

                svc_pool = emp_services[emp.pk]
                slots = emp_slots[emp.pk]

                # Pick 2-4 random slots per day (more on weekdays, fewer on weekends)
                n_slots = random.randint(2, 4) if weekday < 5 else random.randint(1, 3)
                chosen_slots = random.sample(slots, min(n_slots, len(slots)))

                for hour, minute in chosen_slots:
                    client = random.choice(clients)
                    service = random.choice(svc_pool)
                    make_booking(client, emp, service, target_date, hour, minute)

        self.stdout.write(f"  Bookings created: {Booking.objects.count()}")
