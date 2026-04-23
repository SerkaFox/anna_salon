from __future__ import annotations

import os
import sys
from datetime import date, time, timedelta
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "anna_core.settings")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import django

django.setup()

from bookings.models import Booking, BookingPhoto
from clients.models import Client
from employees.models import (
    Employee,
    EmployeeScheduleOverride,
    EmployeeTimeBlock,
    EmployeeWeeklyShift,
)
from salon.models import Zone
from services_app.models import Service
from django.db import transaction
from django.utils import timezone


DEMO_MONTH = date(2026, 4, 1)


def aware_at(day: date, hour: int, minute: int = 0):
    return timezone.make_aware(
        timezone.datetime.combine(day, time(hour=hour, minute=minute))
    )


def money(value: str) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


def create_booking(client, employee, service, day, start_hour, start_minute, status, zone=None, notes="", reward=False):
    start_at = aware_at(day, start_hour, start_minute)
    end_at = start_at + timedelta(minutes=service.duration_minutes)
    original_price = service.price or money("0")
    discount = money("0")
    if reward:
        discount = (original_price * Decimal("20.00") / Decimal("100")).quantize(Decimal("0.01"))

    client_price = original_price - discount
    employee_percent = employee.commission_percent or Decimal("40.00")
    employee_amount = (client_price * employee_percent / Decimal("100")).quantize(Decimal("0.01"))
    salon_amount = client_price - employee_amount

    booking = Booking.objects.create(
        client=client,
        employee=employee,
        service=service,
        zone=zone if service.requires_zone else None,
        start_at=start_at,
        end_at=end_at,
        status=status,
        notes=notes,
        price_snapshot=original_price,
        duration_snapshot=service.duration_minutes,
        original_client_price_snapshot=original_price,
        client_price_snapshot=client_price,
        discount_amount_snapshot=discount,
        referral_reward_applied=reward,
        employee_percent_snapshot=employee_percent,
        employee_amount_snapshot=employee_amount,
        salon_amount_snapshot=salon_amount,
    )
    if reward:
        client.referral_rewards_used += 1
        client.save(update_fields=["referral_rewards_used"])
    return booking


@transaction.atomic
def main():
    BookingPhoto.objects.all().delete()
    Booking.objects.all().delete()
    EmployeeTimeBlock.objects.all().delete()
    EmployeeScheduleOverride.objects.all().delete()
    EmployeeWeeklyShift.objects.all().delete()
    Employee.objects.all().delete()
    Client.objects.all().delete()
    Service.objects.all().delete()
    Zone.objects.all().delete()

    zones = {
        "cabina_1": Zone.objects.create(
            name="Cabina Glow 1",
            zone_type=Zone.ZoneTypes.CABIN,
            capacity=1,
            color="#f3a6b8",
            notes="Cabina principal para faciales, depilacion y tratamientos corporales.",
        ),
        "cabina_2": Zone.objects.create(
            name="Cabina Calm 2",
            zone_type=Zone.ZoneTypes.CABIN,
            capacity=1,
            color="#c9b6ff",
            notes="Cabina silenciosa para tratamientos premium y masajes.",
        ),
        "mesa_mani_1": Zone.objects.create(
            name="Mesa Mani A",
            zone_type=Zone.ZoneTypes.TABLE,
            capacity=1,
            color="#f6c85f",
            notes="Puesto de manicura con lampara LED.",
        ),
        "mesa_mani_2": Zone.objects.create(
            name="Mesa Mani B",
            zone_type=Zone.ZoneTypes.TABLE,
            capacity=1,
            color="#ff9f80",
            notes="Puesto de manicura para refuerzos y nail art.",
        ),
        "makeup": Zone.objects.create(
            name="Tocador Make-up",
            zone_type=Zone.ZoneTypes.MAKEUP,
            capacity=1,
            color="#7bdff2",
            notes="Zona de maquillaje, cejas y laminados.",
        ),
        "wash": Zone.objects.create(
            name="Lavacabezas Spa",
            zone_type=Zone.ZoneTypes.WASH,
            capacity=1,
            color="#b2f7ef",
            notes="Recurso para ritual capilar y tratamientos express.",
        ),
    }

    services = {
        "mani": Service.objects.create(
            name="Manicura semipermanente",
            description="Retirada suave, preparacion, esmaltado semipermanente y aceite final.",
            duration_minutes=75,
            price=money("32.00"),
            requires_zone=True,
        ),
        "gel": Service.objects.create(
            name="Refuerzo gel BIAB",
            description="Nivelacion, refuerzo y acabado natural para unas resistentes.",
            duration_minutes=105,
            price=money("48.00"),
            requires_zone=True,
        ),
        "nail_art": Service.objects.create(
            name="Nail art premium",
            description="Decoracion detallada sobre manicura, ideal para eventos.",
            duration_minutes=120,
            price=money("62.00"),
            requires_zone=True,
        ),
        "pedicure": Service.objects.create(
            name="Pedicura spa",
            description="Pedicura completa con exfoliacion, hidratacion y esmaltado.",
            duration_minutes=90,
            price=money("42.00"),
            requires_zone=True,
        ),
        "facial": Service.objects.create(
            name="Higiene facial glow",
            description="Limpieza profunda, extraccion, mascarilla calmante y SPF.",
            duration_minutes=90,
            price=money("58.00"),
            requires_zone=True,
        ),
        "laser": Service.objects.create(
            name="Depilacion laser zona media",
            description="Sesion laser para axilas, ingles, brazos o zona equivalente.",
            duration_minutes=45,
            price=money("39.00"),
            requires_zone=True,
        ),
        "brows": Service.objects.create(
            name="Diseno y laminado de cejas",
            description="Visagismo, laminado, tinte suave y acabado nutritivo.",
            duration_minutes=60,
            price=money("35.00"),
            requires_zone=True,
        ),
        "makeup": Service.objects.create(
            name="Maquillaje evento",
            description="Maquillaje social completo con preparacion de piel.",
            duration_minutes=90,
            price=money("70.00"),
            requires_zone=True,
        ),
        "hair_ritual": Service.objects.create(
            name="Ritual capilar express",
            description="Lavado, tratamiento hidratante, masaje craneal y peinado rapido.",
            duration_minutes=50,
            price=money("29.00"),
            requires_zone=True,
        ),
        "consult": Service.objects.create(
            name="Consulta estetica inicial",
            description="Valoracion, historial, recomendaciones y plan de tratamiento.",
            duration_minutes=30,
            price=money("0.00"),
            requires_zone=False,
        ),
    }

    services["mani"].allowed_zones.set([zones["mesa_mani_1"], zones["mesa_mani_2"]])
    services["gel"].allowed_zones.set([zones["mesa_mani_1"], zones["mesa_mani_2"]])
    services["nail_art"].allowed_zones.set([zones["mesa_mani_1"], zones["mesa_mani_2"]])
    services["pedicure"].allowed_zones.set([zones["cabina_1"], zones["cabina_2"]])
    services["facial"].allowed_zones.set([zones["cabina_1"], zones["cabina_2"]])
    services["laser"].allowed_zones.set([zones["cabina_1"]])
    services["brows"].allowed_zones.set([zones["makeup"]])
    services["makeup"].allowed_zones.set([zones["makeup"]])
    services["hair_ritual"].allowed_zones.set([zones["wash"]])

    employees = {
        "sofia": Employee.objects.create(
            first_name="Sofia",
            last_name="Marin",
            phone="+34 611 204 018",
            email="sofia.marin@example.com",
            calendar_color="#e85d75",
            commission_percent=Decimal("42.00"),
            notes="Especialista en manicura rusa, gel BIAB y nail art.",
        ),
        "valeria": Employee.objects.create(
            first_name="Valeria",
            last_name="Torres",
            phone="+34 622 319 774",
            email="valeria.torres@example.com",
            calendar_color="#f4a261",
            commission_percent=Decimal("40.00"),
            notes="Faciales, laser y tratamientos corporales.",
        ),
        "irina": Employee.objects.create(
            first_name="Irina",
            last_name="Kovalenko",
            phone="+34 633 118 902",
            email="irina.kovalenko@example.com",
            calendar_color="#2a9d8f",
            commission_percent=Decimal("45.00"),
            notes="Cejas, maquillaje y atencion premium de eventos.",
        ),
        "lucia": Employee.objects.create(
            first_name="Lucia",
            last_name="Santos",
            phone="+34 644 875 521",
            email="lucia.santos@example.com",
            calendar_color="#457b9d",
            commission_percent=Decimal("38.00"),
            notes="Pedicura spa, higiene facial y apoyo en cabina.",
        ),
        "marta": Employee.objects.create(
            first_name="Marta",
            last_name="Rivas",
            phone="+34 655 420 336",
            email="marta.rivas@example.com",
            calendar_color="#8d6b94",
            commission_percent=Decimal("36.00"),
            notes="Ritual capilar express, consultas y recepcion tecnica.",
        ),
    }

    employees["sofia"].services.set([services["mani"], services["gel"], services["nail_art"], services["consult"]])
    employees["valeria"].services.set([services["facial"], services["laser"], services["pedicure"], services["consult"]])
    employees["irina"].services.set([services["brows"], services["makeup"], services["consult"]])
    employees["lucia"].services.set([services["pedicure"], services["facial"], services["laser"], services["consult"]])
    employees["marta"].services.set([services["hair_ritual"], services["consult"], services["brows"]])

    shift_patterns = {
        "sofia": (time(10, 0), time(19, 0), time(14, 0), time(14, 30)),
        "valeria": (time(9, 0), time(18, 0), time(13, 30), time(14, 15)),
        "irina": (time(11, 0), time(20, 0), time(15, 0), time(15, 30)),
        "lucia": (time(9, 30), time(17, 30), time(13, 0), time(13, 45)),
        "marta": (time(10, 0), time(18, 0), time(14, 0), time(14, 30)),
    }
    days_off = {
        "sofia": {6},
        "valeria": {0, 6},
        "irina": {1, 6},
        "lucia": {2, 6},
        "marta": {5, 6},
    }
    for key, employee in employees.items():
        start, end, break_start, break_end = shift_patterns[key]
        for weekday in range(7):
            EmployeeWeeklyShift.objects.create(
                employee=employee,
                weekday=weekday,
                is_day_off=weekday in days_off[key],
                start_time=None if weekday in days_off[key] else start,
                end_time=None if weekday in days_off[key] else end,
                break_start=None if weekday in days_off[key] else break_start,
                break_end=None if weekday in days_off[key] else break_end,
                note="Descanso semanal" if weekday in days_off[key] else "Turno demo abril",
            )

    EmployeeTimeBlock.objects.create(
        employee=employees["sofia"],
        date=date(2026, 4, 15),
        start_time=time(16, 0),
        end_time=time(17, 0),
        label="Formacion producto BIAB",
        color="#111111",
    )
    EmployeeTimeBlock.objects.create(
        employee=employees["irina"],
        date=date(2026, 4, 24),
        start_time=time(12, 30),
        end_time=time(13, 30),
        label="Prueba novia externa",
        color="#111111",
    )
    EmployeeScheduleOverride.objects.create(
        employee=employees["lucia"],
        date=date(2026, 4, 18),
        start_time=time(10, 0),
        end_time=time(15, 0),
        break_start=None,
        break_end=None,
        label="Sabado apertura especial",
    )

    client_rows = [
        ("Ana", "Gomez", "+34 690 114 201", "ana.gomez@example.com", date(1990, 5, 14), "Prefiere tonos nude y citas por la tarde."),
        ("Beatriz", "Luna", "+34 690 114 202", "beatriz.luna@example.com", date(1987, 11, 2), "Cliente recurrente de faciales."),
        ("Camila", "Rey", "+34 690 114 203", "camila.rey@example.com", date(1995, 7, 19), "Le gusta nail art minimalista."),
        ("Daniela", "Soler", "+34 690 114 204", "daniela.soler@example.com", date(1992, 2, 8), "Alergia leve a fragancias intensas."),
        ("Elena", "Vega", "+34 690 114 205", "elena.vega@example.com", date(1984, 9, 22), "Viene normalmente los viernes."),
        ("Fatima", "Nassar", "+34 690 114 206", "fatima.nassar@example.com", date(1998, 1, 30), "Primera visita por recomendacion."),
        ("Gabriela", "Mora", "+34 690 114 207", "gabriela.mora@example.com", date(1991, 12, 4), "Interes en laser mensual."),
        ("Helena", "Costa", "+34 690 114 208", "helena.costa@example.com", date(1989, 6, 18), "Prefiere cabina Calm."),
        ("Ines", "Pardo", "+34 690 114 209", "ines.pardo@example.com", date(1994, 3, 26), "Reserva online, paga con tarjeta."),
        ("Julia", "Ramos", "+34 690 114 210", "julia.ramos@example.com", date(1986, 10, 10), "Tiene evento el 29 de abril."),
        ("Laura", "Nieto", "+34 690 114 211", "laura.nieto@example.com", date(1996, 4, 11), "Cliente recomendadora activa."),
        ("Marta", "Pena", "+34 690 114 212", "marta.pena@example.com", date(1988, 8, 8), "Cejas cada tres semanas."),
        ("Nerea", "Blanco", "+34 690 114 213", "nerea.blanco@example.com", date(1993, 12, 15), "Quiere probar BIAB."),
        ("Olga", "Ivanova", "+34 690 114 214", "olga.ivanova@example.com", date(1982, 6, 1), "Prefiere comunicacion por WhatsApp."),
        ("Paula", "Martin", "+34 690 114 215", "paula.martin@example.com", date(1999, 9, 5), "Estudiante, horarios flexibles."),
        ("Raquel", "Diaz", "+34 690 114 216", "raquel.diaz@example.com", date(1985, 1, 17), "Tratamiento facial mensual."),
        ("Sara", "Lopez", "+34 690 114 217", "sara.lopez@example.com", date(1997, 5, 28), "Pendiente seguimiento de laser."),
        ("Teresa", "Molina", "+34 690 114 218", "teresa.molina@example.com", date(1990, 2, 21), "Consulta regalo de cumpleanos."),
    ]
    clients = {}
    for first_name, last_name, phone, email, birth_date, notes in client_rows:
        clients[f"{first_name} {last_name}"] = Client.objects.create(
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            email=email,
            birth_date=birth_date,
            notes=notes,
        )

    referral_pairs = [
        ("Fatima Nassar", "Ana Gomez"),
        ("Ines Pardo", "Ana Gomez"),
        ("Julia Ramos", "Ana Gomez"),
        ("Nerea Blanco", "Ana Gomez"),
        ("Paula Martin", "Ana Gomez"),
        ("Sara Lopez", "Laura Nieto"),
        ("Teresa Molina", "Laura Nieto"),
        ("Olga Ivanova", "Beatriz Luna"),
    ]
    for child_name, parent_name in referral_pairs:
        clients[child_name].referred_by = clients[parent_name]
        clients[child_name].save(update_fields=["referred_by"])

    booking_specs = [
        ("Ana Gomez", "sofia", "mani", 1, 10, 0, "done", "mesa_mani_1", "Inicio de mes, tono leche."),
        ("Beatriz Luna", "valeria", "facial", 1, 11, 30, "done", "cabina_1", "Piel sensible, mascarilla calmante."),
        ("Camila Rey", "sofia", "gel", 1, 13, 0, "done", "mesa_mani_2", "BIAB natural."),
        ("Daniela Soler", "irina", "brows", 1, 16, 0, "done", "makeup", "Sin perfume."),
        ("Elena Vega", "marta", "hair_ritual", 1, 10, 0, "done", "wash", "Tratamiento hidratante."),
        ("Fatima Nassar", "lucia", "pedicure", 2, 10, 0, "done", "cabina_2", "Primera visita."),
        ("Gabriela Mora", "valeria", "laser", 2, 12, 0, "done", "cabina_1", "Zona axilas."),
        ("Helena Costa", "lucia", "facial", 2, 14, 0, "done", "cabina_2", "Cabina Calm."),
        ("Ines Pardo", "marta", "consult", 2, 16, 0, "done", None, "Plan abril-mayo."),
        ("Julia Ramos", "irina", "makeup", 2, 17, 0, "done", "makeup", "Prueba evento."),
        ("Laura Nieto", "sofia", "nail_art", 3, 10, 30, "done", "mesa_mani_1", "Diseno flores pequenas."),
        ("Marta Pena", "irina", "brows", 3, 12, 0, "done", "makeup", "Mantenimiento."),
        ("Nerea Blanco", "sofia", "mani", 3, 15, 30, "done", "mesa_mani_2", "Primera semi."),
        ("Olga Ivanova", "valeria", "laser", 3, 16, 30, "no_show", "cabina_1", "No asistio, reprogramar."),
        ("Paula Martin", "marta", "hair_ritual", 3, 12, 0, "done", "wash", "Express antes de clase."),
        ("Raquel Diaz", "valeria", "facial", 6, 10, 0, "done", "cabina_1", "Control mensual."),
        ("Sara Lopez", "lucia", "pedicure", 6, 11, 30, "done", "cabina_2", "Color rojo cereza."),
        ("Teresa Molina", "marta", "consult", 6, 15, 0, "done", None, "Regalo cumpleanos."),
        ("Ana Gomez", "sofia", "gel", 7, 11, 0, "done", "mesa_mani_1", "Aplicar premio referido.", True),
        ("Beatriz Luna", "valeria", "laser", 7, 13, 0, "done", "cabina_1", "Zona ingles."),
        ("Camila Rey", "irina", "brows", 7, 17, 30, "done", "makeup", "Tinte castano suave."),
        ("Daniela Soler", "lucia", "facial", 8, 10, 0, "done", "cabina_2", "Sin fragancia."),
        ("Elena Vega", "sofia", "mani", 8, 12, 0, "done", "mesa_mani_1", "Francesa fina."),
        ("Fatima Nassar", "marta", "hair_ritual", 8, 16, 0, "done", "wash", "Masaje extra."),
        ("Gabriela Mora", "valeria", "facial", 9, 9, 30, "done", "cabina_1", "Limpieza express."),
        ("Helena Costa", "sofia", "nail_art", 9, 13, 0, "done", "mesa_mani_2", "Detalle dorado."),
        ("Ines Pardo", "irina", "makeup", 9, 16, 0, "cancelled", "makeup", "Cancelada por cliente."),
        ("Julia Ramos", "marta", "consult", 10, 10, 0, "done", None, "Plan novia invitada."),
        ("Laura Nieto", "sofia", "gel", 10, 12, 0, "done", "mesa_mani_1", "Refuerzo corto."),
        ("Marta Pena", "irina", "brows", 10, 18, 0, "done", "makeup", "Cejas perfectas."),
        ("Nerea Blanco", "lucia", "pedicure", 11, 10, 0, "done", "cabina_2", "Sabado demo."),
        ("Olga Ivanova", "sofia", "mani", 13, 10, 30, "done", "mesa_mani_1", "Reprogramada."),
        ("Paula Martin", "valeria", "laser", 13, 12, 0, "done", "cabina_1", "Zona brazos."),
        ("Raquel Diaz", "lucia", "facial", 13, 14, 0, "done", "cabina_2", "Piel mixta."),
        ("Sara Lopez", "marta", "hair_ritual", 14, 10, 30, "done", "wash", "Brillo rapido."),
        ("Teresa Molina", "irina", "brows", 14, 12, 0, "done", "makeup", "Antes/despues visible."),
        ("Ana Gomez", "sofia", "mani", 14, 17, 0, "done", "mesa_mani_2", "Mantenimiento."),
        ("Beatriz Luna", "valeria", "facial", 15, 10, 0, "done", "cabina_1", "Mascarilla LED."),
        ("Camila Rey", "sofia", "gel", 15, 12, 0, "done", "mesa_mani_1", "Antes del bloqueo."),
        ("Daniela Soler", "lucia", "pedicure", 15, 15, 0, "done", "cabina_2", "Pies sensibles."),
        ("Elena Vega", "marta", "hair_ritual", 16, 10, 0, "done", "wash", "Hidratacion."),
        ("Fatima Nassar", "irina", "makeup", 16, 12, 0, "done", "makeup", "Evento tarde."),
        ("Gabriela Mora", "valeria", "laser", 16, 15, 0, "done", "cabina_1", "Seguimiento."),
        ("Helena Costa", "lucia", "facial", 17, 10, 0, "done", "cabina_2", "Cabina Calm."),
        ("Ines Pardo", "sofia", "nail_art", 17, 12, 0, "done", "mesa_mani_1", "Geometrico nude."),
        ("Julia Ramos", "irina", "makeup", 17, 17, 0, "done", "makeup", "Evento confirmado."),
        ("Laura Nieto", "lucia", "pedicure", 18, 10, 0, "done", "cabina_2", "Apertura especial."),
        ("Marta Pena", "marta", "consult", 20, 10, 0, "done", None, "Nueva rutina cejas."),
        ("Nerea Blanco", "sofia", "gel", 20, 12, 0, "done", "mesa_mani_2", "BIAB rosa."),
        ("Olga Ivanova", "valeria", "facial", 20, 14, 30, "done", "cabina_1", "Recuperacion post laser."),
        ("Paula Martin", "irina", "brows", 21, 11, 30, "done", "makeup", "Laminado natural."),
        ("Raquel Diaz", "marta", "hair_ritual", 21, 13, 0, "done", "wash", "Masaje craneal."),
        ("Sara Lopez", "valeria", "laser", 21, 15, 30, "done", "cabina_1", "Zona media."),
        ("Teresa Molina", "sofia", "mani", 22, 10, 30, "done", "mesa_mani_1", "Color pastel."),
        ("Ana Gomez", "irina", "brows", 22, 12, 0, "done", "makeup", "Repaso cejas."),
        ("Beatriz Luna", "lucia", "facial", 22, 14, 0, "done", "cabina_2", "Alta frecuencia."),
        ("Camila Rey", "marta", "consult", 23, 10, 0, "confirmed", None, "Consulta hoy."),
        ("Daniela Soler", "sofia", "mani", 23, 12, 0, "in_progress", "mesa_mani_1", "Servicio en curso."),
        ("Elena Vega", "valeria", "laser", 23, 15, 0, "confirmed", "cabina_1", "Confirmada por WhatsApp."),
        ("Fatima Nassar", "irina", "makeup", 23, 17, 0, "confirmed", "makeup", "Maquillaje tarde."),
        ("Gabriela Mora", "sofia", "gel", 24, 10, 30, "confirmed", "mesa_mani_2", "Confirmada."),
        ("Helena Costa", "lucia", "facial", 24, 12, 0, "confirmed", "cabina_2", "Control mensual."),
        ("Ines Pardo", "marta", "hair_ritual", 24, 15, 0, "pending", "wash", "Pendiente confirmar."),
        ("Julia Ramos", "irina", "brows", 24, 18, 0, "confirmed", "makeup", "Despues de bloqueo."),
        ("Laura Nieto", "sofia", "nail_art", 27, 10, 30, "confirmed", "mesa_mani_1", "Diseno evento."),
        ("Marta Pena", "valeria", "facial", 27, 11, 0, "pending", "cabina_1", "Pendiente deposito."),
        ("Nerea Blanco", "lucia", "pedicure", 27, 14, 0, "confirmed", "cabina_2", "Pedicura completa."),
        ("Olga Ivanova", "marta", "consult", 27, 16, 0, "confirmed", None, "Nueva valoracion."),
        ("Paula Martin", "irina", "makeup", 28, 12, 0, "confirmed", "makeup", "Graduacion."),
        ("Raquel Diaz", "sofia", "mani", 28, 15, 0, "pending", "mesa_mani_2", "Pendiente color."),
        ("Sara Lopez", "valeria", "laser", 28, 16, 30, "confirmed", "cabina_1", "Seguimiento."),
        ("Teresa Molina", "marta", "hair_ritual", 29, 10, 0, "confirmed", "wash", "Antes de viaje."),
        ("Ana Gomez", "sofia", "gel", 29, 12, 0, "confirmed", "mesa_mani_1", "Mantenimiento BIAB."),
        ("Beatriz Luna", "lucia", "facial", 29, 14, 0, "pending", "cabina_2", "Pendiente confirmar."),
        ("Camila Rey", "irina", "brows", 29, 17, 0, "confirmed", "makeup", "Repaso abril."),
        ("Daniela Soler", "sofia", "mani", 30, 10, 30, "confirmed", "mesa_mani_2", "Cierre de mes."),
        ("Elena Vega", "valeria", "facial", 30, 12, 0, "confirmed", "cabina_1", "Facial glow."),
        ("Fatima Nassar", "marta", "consult", 30, 15, 0, "pending", None, "Plan mayo."),
        ("Gabriela Mora", "irina", "makeup", 30, 17, 0, "confirmed", "makeup", "Cena empresa."),
    ]

    source_cycle = [
        "website",
        "whatsapp",
        "manual",
        "instagram",
        "phone",
        "walk_in",
        "rebooking",
        "referral",
        "google",
    ]

    for index, spec in enumerate(booking_specs):
        reward = False
        if len(spec) == 10:
            client_name, employee_key, service_key, day_num, hour, minute, status, zone_key, notes, reward = spec
        else:
            client_name, employee_key, service_key, day_num, hour, minute, status, zone_key, notes = spec
        booking = create_booking(
            client=clients[client_name],
            employee=employees[employee_key],
            service=services[service_key],
            day=DEMO_MONTH.replace(day=day_num),
            start_hour=hour,
            start_minute=minute,
            status=status,
            zone=zones[zone_key] if zone_key else None,
            notes=notes,
            reward=reward,
        )
        booking.source = source_cycle[index % len(source_cycle)]
        booking.save(update_fields=["source"])

    print(
        {
            "zones": Zone.objects.count(),
            "services": Service.objects.count(),
            "employees": Employee.objects.count(),
            "weekly_shifts": EmployeeWeeklyShift.objects.count(),
            "time_blocks": EmployeeTimeBlock.objects.count(),
            "schedule_overrides": EmployeeScheduleOverride.objects.count(),
            "clients": Client.objects.count(),
            "bookings": Booking.objects.count(),
            "done_bookings": Booking.objects.filter(status=Booking.Statuses.DONE).count(),
            "future_or_active_bookings": Booking.objects.exclude(status=Booking.Statuses.DONE).count(),
        }
    )


if __name__ == "__main__":
    main()
