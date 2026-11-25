from django.db import migrations, models


class Migration(migrations.Migration):
    """Adds "ERP Meeting" to the room-booking purposes."""

    dependencies = [('roompulse', '0021_conference_room_seats')]

    operations = [
        migrations.AlterField(
            model_name='bookingrequest',
            name='purpose',
            field=models.CharField(
                choices=[('client_meeting', 'Client Meeting'), ('internal_meeting', 'Internal Team Meeting'),
                         ('interview', 'Interview'), ('training', 'Training / Workshop'),
                         ('board_meeting', 'Board Meeting'), ('presentation', 'Presentation'),
                         ('vendor_meeting', 'Vendor Meeting'), ('erp_meeting', 'ERP Meeting'),
                         ('other', 'Other')],
                default='internal_meeting', max_length=30),
        ),
    ]
