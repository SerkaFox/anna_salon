from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("salon", "0002_salonsettings"),
    ]

    operations = [
        migrations.AddField(
            model_name="salonsettings",
            name="receipt_address",
            field=models.CharField(
                default="Rafaela Ybarra Kalea, 2 bis, Deusto, 48014 Bilbao, Bizkaia",
                max_length=255,
                verbose_name="Direccion en el recibo",
            ),
        ),
        migrations.AddField(
            model_name="salonsettings",
            name="receipt_business_name",
            field=models.CharField(
                default="BRIMOON Studio",
                max_length=160,
                verbose_name="Nombre en el recibo",
            ),
        ),
        migrations.AddField(
            model_name="salonsettings",
            name="receipt_email",
            field=models.EmailField(
                blank=True,
                max_length=254,
                verbose_name="Email en el recibo",
            ),
        ),
        migrations.AddField(
            model_name="salonsettings",
            name="receipt_footer",
            field=models.CharField(
                blank=True,
                default="Gracias por tu visita :)",
                max_length=240,
                verbose_name="Mensaje final del recibo",
            ),
        ),
        migrations.AddField(
            model_name="salonsettings",
            name="receipt_phone",
            field=models.CharField(
                blank=True,
                default="643996431",
                max_length=40,
                verbose_name="Telefono en el recibo",
            ),
        ),
        migrations.AddField(
            model_name="salonsettings",
            name="receipt_show_logo",
            field=models.BooleanField(
                default=True,
                verbose_name="Mostrar logotipo",
            ),
        ),
        migrations.AddField(
            model_name="salonsettings",
            name="receipt_show_qr",
            field=models.BooleanField(
                default=True,
                verbose_name="Mostrar codigo QR",
            ),
        ),
        migrations.AddField(
            model_name="salonsettings",
            name="receipt_website",
            field=models.URLField(
                blank=True,
                default="https://brimoon.es",
                verbose_name="Web en el recibo",
            ),
        ),
    ]
