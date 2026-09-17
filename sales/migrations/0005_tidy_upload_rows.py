"""Make the uploaded-files list agree with the table underneath it.

Two faults put rows in that list that did not describe reality, and both are
fixed in the code — this clears up what they already left behind.

A sheet that failed used to set a status and delete its records without
deleting the upload row, so an empty "0 rows - Rs 0" line stayed in the list
for good. And each sheet of a workbook created its own upload row, so a
two-tab file appeared twice under one name while the header total counted
every row once.

Empty upload rows are removed. The rest are recounted from the rows they
actually own, with revenue taken over what was not cancelled — the same rule
the dashboards use, so the list and the figures can no longer disagree.
"""
from django.db import migrations
from django.db.models import Count, Max, Min, Sum


def tidy(apps, schema_editor):
    SalesUpload = apps.get_model('sales', 'SalesUpload')

    SalesUpload.objects.annotate(n=Count('records')).filter(n=0).delete()

    for upload in SalesUpload.objects.all().iterator(chunk_size=100):
        agg = upload.records.aggregate(
            n=Count('id'), lo=Min('order_date'), hi=Max('order_date'))
        earned = upload.records.exclude(is_cancelled=True).aggregate(
            rev=Sum('net_amount'))['rev']
        upload.row_count = agg['n'] or 0
        upload.total_revenue = round(float(earned or 0), 2)
        upload.period_start = agg['lo']
        upload.period_end = agg['hi']
        upload.status = 'completed'
        upload.save(update_fields=['row_count', 'total_revenue', 'period_start',
                                   'period_end', 'status'])


def noop(apps, schema_editor):
    """Nothing to undo: this only corrects counts to match the rows that are
    there, and deletes rows that described nothing."""


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0004_salesrecord_plan_achievement'),
    ]

    operations = [migrations.RunPython(tidy, noop)]
