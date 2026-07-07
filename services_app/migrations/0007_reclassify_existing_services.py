from django.db import migrations


SERVICE_CATEGORY_BY_NAME = {
    "Cortar y Limar Manos": "manicure",
    "Extensiones de Gel (Largura Media) con Decoración": "manicure",
    "Primera Puesta Extensiones de Gel (Cortas) – Color Liso": "manicure",
    "Primera Puesta Extensiones de Gel (Cortas)- Decorasion fino": "manicure",
    "Primera Puesta Extensiones de Gel (Cortas) - francesa": "manicure",
    "Primera Puesta Extensiones Polygel Largas XL": "manicure",
    "Primera Puesta Extensiones Polygel Largas XL (decorada)": "manicure",
    "Relleno de gel": "manicure",
    "Retirar gel": "manicure",
    "Retirar Semipermanente": "manicure",
    "Semipermanente con Refuerzo": "manicure",
    "Quitar Durezas": "pedicure",
}


def reclassify_existing_services(apps, schema_editor):
    Service = apps.get_model("services_app", "Service")
    for service in Service.objects.filter(name__in=SERVICE_CATEGORY_BY_NAME):
        expected_category = SERVICE_CATEGORY_BY_NAME[service.name]
        if service.category != expected_category:
            service.category = expected_category
            service.save(update_fields=["category"])


class Migration(migrations.Migration):

    dependencies = [
        ("services_app", "0006_service_category"),
    ]

    operations = [
        migrations.RunPython(reclassify_existing_services, migrations.RunPython.noop),
    ]
