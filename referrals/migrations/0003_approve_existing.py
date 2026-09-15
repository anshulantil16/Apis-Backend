"""Referrals already in the table were never awaiting approval.

A referral goes to HR rather than onto the dashboard, so it has no publish
step (see the model docstring). New ones are marked approved on arrival; this
does the same for the rows that predate the column, so the console's queue
shows only things that genuinely need a decision.
"""
from django.db import migrations


def approve_existing(apps, schema_editor):
    Referral = apps.get_model('referrals', 'EmployeeReferral')
    Referral.objects.filter(moderation_status='pending').update(moderation_status='approved')


class Migration(migrations.Migration):

    dependencies = [
        ('referrals', '0002_employeereferral_moderation_status_and_more'),
    ]

    operations = [migrations.RunPython(approve_existing, migrations.RunPython.noop)]
