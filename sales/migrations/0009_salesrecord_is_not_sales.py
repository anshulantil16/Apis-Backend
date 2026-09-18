"""The business's own "NOT A PART OF SALES" flag, read from V-REMARS.

Backfilled from the remark text for rows already loaded, so an existing
upload does not have to be re-imported to stop counting freight as revenue.
"""
from django.db import migrations, models


def flag_existing(apps, schema_editor):
    SalesRecord = apps.get_model('sales', 'SalesRecord')
    SalesRecord.objects.filter(remarks__iexact='NOT A PART OF SALES').update(
        is_not_sales=True)
    for remark in ('SR', 'GOOD SR', 'SCHEME CN'):
        SalesRecord.objects.filter(remarks__iexact=remark).update(is_return=True)


def unflag(apps, schema_editor):
    SalesRecord = apps.get_model('sales', 'SalesRecord')
    SalesRecord.objects.update(is_not_sales=False)


class Migration(migrations.Migration):

    dependencies = [('sales', '0008_measured_amount')]

    operations = [
        migrations.AddField(
            model_name='salesrecord',
            name='is_not_sales',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.RunPython(flag_existing, unflag),
    ]
