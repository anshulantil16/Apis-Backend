"""Re-flag the lines the business excludes from sales.

SCHEME CN was classified as a return, which nets it off by sign instead of
dropping it, so every date-wise figure read lower than the sheet by the value
of those lines. The rule is now: NOT A PART OF SALES and SCHEME CN are
excluded; SR and GOOD SR are genuine returns and stay in.

The flags are decided at import, so changing the importer fixes future
uploads and nothing already loaded. This re-runs the new rule over the rows
that are already there.

Reverse puts SCHEME CN back to being a return, so the migration is honest
about what it changed rather than claiming to be irreversible.
"""
from django.db import migrations
from django.db.models import Q


def _candidates(SalesRecord):
    """Narrow to rows worth testing before applying the exact predicate.

    is_not_a_sale normalises case and whitespace, which SQL will not do for
    us, so the precise test happens in Python -- but iterating every invoice
    line to find a few hundred would be wasteful.
    """
    return SalesRecord.objects.filter(
        Q(remarks__icontains='scheme cn') | Q(remarks__icontains='part of sale')
    ).exclude(remarks='')


def apply_rule(apps, schema_editor):
    from sales.ingest import is_not_a_sale
    SalesRecord = apps.get_model('sales', 'SalesRecord')

    to_exclude = [r.pk for r in _candidates(SalesRecord).only('pk', 'remarks')
                  if is_not_a_sale(r.remarks)]
    if to_exclude:
        # A line is classified once: excluded, or a return, never both.
        SalesRecord.objects.filter(pk__in=to_exclude).update(
            is_not_sales=True, is_return=False)


def restore(apps, schema_editor):
    from sales.ingest import _norm
    SalesRecord = apps.get_model('sales', 'SalesRecord')

    back = [r.pk for r in _candidates(SalesRecord).only('pk', 'remarks')
            if _norm(r.remarks) == _norm('scheme cn')]
    if back:
        SalesRecord.objects.filter(pk__in=back).update(
            is_not_sales=False, is_return=True)


class Migration(migrations.Migration):

    dependencies = [('sales', '0011_clear_posting_group_channel')]

    operations = [migrations.RunPython(apply_rule, restore)]
