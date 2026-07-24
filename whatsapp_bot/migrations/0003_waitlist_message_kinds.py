from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('whatsapp_bot', '0002_whatsapp_template'),
    ]

    operations = [
        migrations.AlterField(
            model_name='whatsappmessage',
            name='kind',
            field=models.CharField(
                choices=[
                    ('waitlist_joined', 'Waitlist joined'),
                    ('waitlist_slot_available', 'Waitlist slot available'),
                    ('booking_confirmation', 'Booking confirmation'),
                    ('booking_cancelled', 'Booking cancelled'),
                    ('booking_rescheduled', 'Booking rescheduled'),
                    ('reminder_24h', 'Reminder 24h'),
                    ('reminder_2h', 'Reminder 2h'),
                    ('welcome_credentials', 'Welcome / login credentials'),
                    ('payment_receipt', 'Payment receipt'),
                    ('birthday_greeting', 'Birthday greeting'),
                    ('manual', 'Manual'),
                ],
                max_length=40,
            ),
        ),
        migrations.AlterField(
            model_name='whatsapptemplate',
            name='kind',
            field=models.CharField(
                choices=[
                    ('waitlist_joined', 'Waitlist joined'),
                    ('waitlist_slot_available', 'Waitlist slot available'),
                    ('booking_confirmation', 'Booking confirmation'),
                    ('booking_cancelled', 'Booking cancelled'),
                    ('booking_rescheduled', 'Booking rescheduled'),
                    ('reminder_24h', 'Reminder 24h'),
                    ('reminder_2h', 'Reminder 2h'),
                    ('welcome_credentials', 'Welcome / login credentials'),
                    ('payment_receipt', 'Payment receipt'),
                    ('birthday_greeting', 'Birthday greeting'),
                    ('manual', 'Manual'),
                ],
                max_length=40,
                unique=True,
            ),
        ),
    ]
