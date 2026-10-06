"""Show how each SalesIQ tile is arrived at, one filter at a time.

The tiles kept disagreeing with what the business counts by hand, and a
single wrong total says nothing about which rule caused it. This walks the
dump through the same filters the dashboard applies, in the same order, and
prints the four figures after each one -- so the step where a number stops
matching is the rule to argue with.

    python manage.py salesiq_check
    python manage.py salesiq_check --month 2026-09

Read-only. It writes nothing.
"""
from django.core.management.base import BaseCommand
from django.db.models import Count, Sum, Q

from sales.models import SalesRecord, sheet_months
from sales.views.core import FINISHED_GOODS_PREFIX
from sales.views.filters import (NOT_SALES_ZONES, _not_sales_zone_q,
                                 in_plan_scope, sheet_zones)


def tiles(qs):
    """The four tiles, exactly as the overview endpoint counts them."""
    agg = qs.aggregate(
        orders=Count('sales_order_no', distinct=True,
                     filter=~Q(sales_order_no='')),
        qty=Sum('quantity'))
    return {
        'orders': agg['orders'] or 0,
        'quantity': float(agg['qty'] or 0),
        'customers': qs.exclude(customer_code='')
                       .values('customer_code').distinct().count(),
        'skus': qs.filter(sku__istartswith=FINISHED_GOODS_PREFIX)
                  .values('sku').distinct().count(),
    }


class Command(BaseCommand):
    help = 'Show how each SalesIQ tile is arrived at, one filter at a time.'

    def add_arguments(self, parser):
        parser.add_argument('--month', help='Limit to one month, as YYYY-MM.')

    def handle(self, *args, **opts):
        w = self.stdout.write

        dump = SalesRecord.objects.filter(source=SalesRecord.SOURCE_INVOICE)
        if opts.get('month'):
            y, m = opts['month'].split('-')
            dump = dump.filter(order_date__year=int(y), order_date__month=int(m))
            w(f'Month filter: {opts["month"]}')

        w('')
        w('ROWS IN THE DUMP')
        w(f'  all invoice rows                {dump.count():>10,}')

        steps = [
            ('minus cancelled',           dump.exclude(is_cancelled=True)),
        ]
        a = steps[-1][1]
        steps.append(('minus NOT A PART OF SALES + SCHEME CN',
                      a.exclude(is_not_sales=True)))
        b = steps[-1][1]
        keep = sheet_zones()
        label = ('keep only zones the review sheet has'
                 if keep else f'minus zones {NOT_SALES_ZONES} (no sheet loaded)')
        steps.append((label, in_plan_scope(b)))
        final = steps[-1][1]

        for label, qs in steps:
            w(f'  {label:<32}{qs.count():>10,}')

        w('')
        w('THE FOUR TILES, AFTER EACH FILTER')
        w(f'  {"stage":<40}{"orders":>9}{"quantity":>12}'
          f'{"customers":>11}{"skus":>7}')
        rows = [('raw dump', dump)] + steps
        for label, qs in rows:
            t = tiles(qs)
            w(f'  {label:<40}{t["orders"]:>9,}{t["quantity"]:>12,.0f}'
              f'{t["customers"]:>11,}{t["skus"]:>7,}')

        w('')
        w('WHAT EACH TILE COUNTS')
        w('  orders     distinct Sales Order No., blanks excluded')
        w('  quantity   sum of Quantity')
        w('  customers  distinct Customer No., blanks excluded')
        w(f'  skus       distinct Item Code starting {FINISHED_GOODS_PREFIX!r}')

        # What the dashboard adds on top: a financial-year window, which the
        # tiles inherit. If the hand count covers the whole file and this
        # covers one year, that difference alone explains a mismatch.
        w('')
        w('THE WINDOW THE DASHBOARD APPLIES ON TOP')
        lo = final.order_by('order_date').values_list('order_date', flat=True).first()
        hi = final.order_by('-order_date').values_list('order_date', flat=True).first()
        w(f'  dump covers                     {lo} -> {hi}')
        w(f'  months the review sheet answers {len(sheet_months())}')
        w('  the dashboard defaults to the current financial year, so a tile')
        w('  covers only the dump rows inside it. Compare a hand count over')
        w('  the whole file against the "minus zones" line above, not against')
        w('  the screen.')

        w('')
        w('SKU CODES NOT COUNTED (first 15 distinct, for sanity)')
        skipped = (final.exclude(sku__istartswith=FINISHED_GOODS_PREFIX)
                        .exclude(sku='').order_by()
                        .values_list('sku', flat=True).distinct()[:15])
        w('  ' + (', '.join(skipped) if skipped else '(none)'))

        w('')
        w('ZONES: WHAT THE PLAN COVERS, AND WHAT IT DOES NOT')
        in_dump = sorted({z for z in dump.exclude(zone='').order_by()
                          .values_list('zone', flat=True).distinct()})
        plan_zones = sorted(sheet_zones())
        w('  the review sheet has : ' + (', '.join(plan_zones) or '(none)'))
        w('  the dump has         : ' + (', '.join(in_dump) or '(none)'))
        dropped = [z for z in in_dump if z not in plan_zones]
        w('  DROPPED, no plan for : ' + (', '.join(dropped) or '(none)'))
        if not plan_zones:
            w('  no sheet loaded, so the named-zone fallback is in use')

        w('')
        w('CHANNEL VALUES PRESENT (blank means the dump needs re-uploading)')
        chans = (dump.exclude(channel='').order_by()
                     .values_list('channel', flat=True).distinct()[:25])
        blank = dump.filter(channel='').count()
        w('  ' + (', '.join(chans) if chans else '(none)'))
        w(f'  rows with no channel            {blank:>10,}')
