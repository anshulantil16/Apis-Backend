"""Real seat counts for the three conference rooms (they were all seeded
with 12): Room 1 seats 14, Room 2 seats 10, Room 3 seats 30. Matched by
name, so a room that was renamed or removed is left alone."""
from django.db import migrations

SEATS = {'Conference Room - 1': 14, 'Conference Room - 2': 10, 'Conference Room - 3': 30}


def set_seats(apps, schema_editor):
    Room = apps.get_model('roompulse', 'Room')
    for name, seats in SEATS.items():
        Room.objects.filter(name=name).update(capacity=seats)


class Migration(migrations.Migration):
    dependencies = [('roompulse', '0020_alter_ticketevent_action_handover')]
    operations = [migrations.RunPython(set_seats, migrations.RunPython.noop)]
