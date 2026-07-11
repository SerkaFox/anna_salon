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

        def make_booking(client, employee, service, day_offset, hour, minute=0, status=Booking.Statuses.CONFIRMED):
            target_date = today + timedelta(days=day_offset)
            start = timezone.make_aware(timezone.datetime.combine(target_date, time(hour, minute)))
            end = start + timedelta(minutes=service.duration_minutes)
            price = service.price
            commission = price * (employee.commission_percent / 100)
            existing = Booking.objects.filter(client=client, employee=employee, start_at=start).first()
            if not existing:
                Booking.objects.create(
                    client=client, employee=employee, service=service,
                    start_at=start, end_at=end, status=status,
                    source=Booking.Sources.WEBSITE if day_offset < 0 else Booking.Sources.MANUAL,
                    price_snapshot=price, client_price_snapshot=price,
                    employee_amount_snapshot=commission,
                )

        ana, sofia, carmen = employees[0], employees[1], employees[2]
        manicura_b = next(s for s in services if s.name == "Manicura básica")
        manicura_s = next(s for s in services if s.name == "Manicura semipermanente")
        nail_art   = next(s for s in services if s.name == "Manicura con nail art")
        pedicura   = next(s for s in services if s.name == "Pedicura completa")
        pedi_semi  = next(s for s in services if s.name == "Pedicura semipermanente")
        cejas      = next(s for s in services if s.name == "Diseño de cejas")
        laminado   = next(s for s in services if s.name == "Laminado de cejas")
        lifting    = next(s for s in services if s.name == "Lifting de pestañas")
        extensiones = next(s for s in services if s.name == "Extensiones de pestañas")
        facial     = next(s for s in services if s.name == "Facial hidratante")
        depilacion = next(s for s in services if s.name == "Depilación piernas completas")

        c = clients  # shortcut

        # --- PAST BOOKINGS (last 60 days) ---
        past = [
            (c[0], ana,    manicura_s, -60, 10), (c[1], sofia,  lifting,    -58, 11),
            (c[2], carmen, facial,     -55, 10), (c[3], ana,    manicura_b, -52, 9),
            (c[4], sofia,  cejas,      -50, 12), (c[5], carmen, pedicura,   -48, 10),
            (c[6], ana,    nail_art,   -45, 11), (c[7], sofia,  laminado,   -43, 14),
            (c[8], carmen, depilacion, -40, 10), (c[9], ana,    manicura_s, -38, 9),
            (c[0], sofia,  cejas,      -35, 11), (c[1], carmen, facial,     -33, 15),
            (c[2], ana,    pedicura,   -30, 10), (c[3], sofia,  lifting,    -28, 13),
            (c[4], carmen, pedi_semi,  -25, 11), (c[5], ana,    manicura_b, -23, 9),
            (c[6], sofia,  extensiones,-20, 10), (c[7], carmen, depilacion, -18, 15),
            (c[8], ana,    nail_art,   -15, 11), (c[9], sofia,  laminado,   -13, 12),
            (c[0], carmen, facial,     -12, 10), (c[1], ana,    manicura_s, -10, 9),
            (c[2], sofia,  cejas,      -8,  11), (c[3], carmen, pedicura,   -7,  14),
            (c[4], ana,    manicura_b, -6,  10), (c[5], sofia,  lifting,    -5,  13),
            (c[6], carmen, pedi_semi,  -4,  11), (c[7], ana,    nail_art,   -3,  9),
            (c[8], sofia,  extensiones,-2,  10), (c[9], carmen, depilacion, -1,  15),
        ]
        for args in past:
            make_booking(*args)

        # --- UPCOMING BOOKINGS (next 30 days) ---
        upcoming = [
            (c[0], ana,    manicura_s,  2, 10), (c[1], sofia,  lifting,    3, 11),
            (c[2], carmen, facial,      4, 10), (c[3], ana,    manicura_b, 5, 9),
            (c[4], sofia,  cejas,       6, 12), (c[5], carmen, pedicura,   7, 10),
            (c[6], ana,    nail_art,    8, 11), (c[7], sofia,  laminado,   9, 14),
            (c[8], carmen, depilacion, 10, 10), (c[9], ana,    manicura_s, 11, 9),
            (c[0], sofia,  extensiones,12, 11), (c[1], carmen, facial,     14, 15),
            (c[2], ana,    pedicura,   15, 10), (c[3], sofia,  lifting,    16, 13),
            (c[4], carmen, pedi_semi,  17, 11), (c[5], ana,    manicura_b, 18, 9),
            (c[6], sofia,  cejas,      19, 10), (c[7], carmen, pedi_semi,  20, 15),
            (c[8], ana,    nail_art,   21, 11), (c[9], sofia,  laminado,   22, 12),
            (c[0], carmen, facial,     23, 10), (c[1], ana,    manicura_s, 24, 9),
            (c[2], sofia,  lifting,    25, 11), (c[3], carmen, depilacion, 26, 14),
            (c[4], ana,    manicura_b, 27, 10), (c[5], sofia,  extensiones,28, 13),
            (c[6], carmen, pedicura,   29, 11), (c[7], ana,    nail_art,   30, 9),
        ]
        for args in upcoming:
            make_booking(*args)
