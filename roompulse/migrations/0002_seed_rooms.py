"""Seed the three real APIS conference rooms so they exist on every
environment automatically — no manual Super Admin setup required on a fresh
deploy. Idempotent (get_or_create) so re-running is harmless."""
from django.db import migrations

ROOMS = [
    {'name': 'Conference Room - 1', 'label': '(Apis)',     'floor': '1st Floor', 'color': '#f59e0b'},
    {'name': 'Conference Room - 2', 'label': '(Misk)',      'floor': '2nd Floor', 'color': '#6366f1'},
    {'name': 'Conference Room - 3', 'label': '(Nutrasip)',  'floor': '2nd Floor', 'color': '#10b981'},
]


def seed(apps, schema_editor):
    Room = apps.get_model('roompulse', 'Room')
    for r in ROOMS:
        Room.objects.get_or_create(name=r['name'], floor=r['floor'], defaults={
            'label': r['label'], 'color': r['color'], 'capacity': 12,
            'amenities': ['Projector', 'Whiteboard', 'Video Conferencing'],
        })


def unseed(apps, schema_editor):
    Room = apps.get_model('roompulse', 'Room')
    for r in ROOMS:
        Room.objects.filter(name=r['name'], floor=r['floor']).delete()


class Migration(migrations.Migration):
    dependencies = [('roompulse', '0001_initial')]
    operations = [migrations.RunPython(seed, unseed)]
