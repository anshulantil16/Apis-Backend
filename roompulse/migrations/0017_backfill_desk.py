"""Put the logged work already in the table on the right desk.

Everything defaults to 'it', which is right for tickets and for IT's own
logged jobs but wrong for the admin work logged since the categories were
opened up. The category is the only evidence those rows carry, and it is
enough: the two lists overlap only on 'other', which stays where it is
rather than being guessed at.
"""
from django.db import migrations


def set_desk(apps, schema_editor):
    SupportTicket = apps.get_model('roompulse', 'SupportTicket')
    # Written out rather than imported from models: a migration has to keep
    # working when that list changes.
    admin_only = [
        'stationery_office_supplies', 'housekeeping', 'pantry_refreshments',
        'furniture_seating', 'facility_maintenance', 'electricity_lighting',
        'plumbing', 'security_access', 'id_card_employee_badge',
        'courier_dispatch', 'travel_accommodation', 'cab_transportation',
        'meeting_room', 'office_equipment', 'printing_photocopy',
        'events_administration', 'vendor_service_request', 'workplace_safety',
        'general_administration',
    ]
    SupportTicket.objects.filter(origin='logged', category__in=admin_only).update(desk='admin')


def back(apps, schema_editor):
    apps.get_model('roompulse', 'SupportTicket').objects.update(desk='it')


class Migration(migrations.Migration):
    dependencies = [('roompulse', '0016_supportticket_desk')]
    operations = [migrations.RunPython(set_desk, back)]
