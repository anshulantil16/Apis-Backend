"""plan_achievement becomes measured_amount, and invoice rows start using it.

The field was added for one job — parking the AOP sheet's achievement columns
somewhere they would not be summed twice — and the reconciliation has since
grown to elect a source per month for both files. It now holds every row's own
measured figure whatever file the row came from, so the old name described a
third of its contents.

Invoice rows loaded before this have the figure only in net_amount, and
sync_actual_source() now reads exclusively from measured_amount: without the
backfill it would elect every historic invoice row to zero.
"""
from django.db import migrations, models


def fill_measured(apps, schema_editor):
    SalesRecord = apps.get_model('sales', 'SalesRecord')
    SalesRecord.objects.filter(source='invoice').update(
        measured_amount=models.F('net_amount'))


def unfill(apps, schema_editor):
    """Nothing to undo: net_amount was never the derived side for invoices."""


class Migration(migrations.Migration):

    dependencies = [('sales', '0007_salesrecord_line_type')]

    operations = [
        migrations.RenameField(
            model_name='salesrecord',
            old_name='plan_achievement',
            new_name='measured_amount',
        ),
        migrations.RunPython(fill_measured, unfill),
    ]
