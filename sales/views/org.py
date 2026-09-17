"""The selling organisation, and what each part of it sold.

Both primary-sales files carry the reporting line — HEAD above RSM above ASM —
but every other view flattens it, so you could rank ASMs against each other and
never see which RSM they answered to, or how many of them an RSM carries.

This assembles the tree in one query and rolls the figures up it. A level with
no name is dropped rather than shown as a blank branch: the Pre-Sales Dump has
RSM and ASM but no head, and an empty root labelled "" is noise, not structure.
"""
from django.db.models import Count, Max, Sum
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import SalesRecord
from .filters import apply_filters


def _pct(part, whole):
    """Achievement %. None when there is no target — 0 would read as 'missed
    the plan', which is a different and untrue statement."""
    if not whole:
        return None
    return round((part / whole) * 100, 1)


def _node(name, level):
    return {
        'name': name, 'level': level,
        'revenue': 0.0, 'target': 0.0, 'quantity': 0.0, 'lines': 0,
        'customers': 0, 'states': 0, 'areas': 0, 'skus': 0, 'field_officers': 0,
        'children': [],
    }


def _finish(node):
    """Sort children by revenue, roll counts up, and compute achievement."""
    for child in node['children']:
        _finish(child)
    if node['children']:
        node['children'].sort(key=lambda c: c['revenue'], reverse=True)
        # Counts of PEOPLE come from the tree; counts of things a person
        # covers come from the rows, and are summed rather than deduplicated
        # because two ASMs selling in one state is two pieces of coverage.
        node['reports'] = len(node['children'])
        node['team'] = sum(c.get('team', 0) or 1 for c in node['children'])
    else:
        node['reports'] = 0
        node['team'] = 0
    node['achievement_pct'] = _pct(node['revenue'], node['target'])
    node['revenue'] = round(node['revenue'], 2)
    node['target'] = round(node['target'], 2)
    return node


class SalesOrgView(APIView):
    """GET — the reporting tree with each level's sales rolled up into it.

    `?levels=sales_head,rsm,asm` to change the shape; the default is the full
    line. Any dimension the breakdown accepts can be a level, so
    `?levels=zone,state,area` gives the same treatment to geography.
    """

    DEFAULT_LEVELS = ['sales_head', 'rsm', 'asm']
    ALLOWED = {'sales_head', 'rsm', 'asm', 'salesperson', 'zone', 'subzone',
               'state', 'area', 'city', 'channel', 'business_type', 'location',
               'category', 'sub_category', 'brand'}

    def get(self, request):
        raw = (request.query_params.get('levels') or '').strip()
        levels = [f for f in (x.strip() for x in raw.split(',')) if f in self.ALLOWED]
        if not levels:
            levels = list(self.DEFAULT_LEVELS)

        qs, applied = apply_filters(SalesRecord.objects.all(), request)

        rows = (qs.values(*levels)
                  .annotate(revenue=Sum('net_amount'), target=Sum('target_amount'),
                            quantity=Sum('quantity'), lines=Count('id'),
                            customers=Count('customer_name', distinct=True),
                            states=Count('state', distinct=True),
                            areas=Count('subzone', distinct=True),
                            skus=Count('sku', distinct=True),
                            field_officers=Max('sfo_count'))
                  .order_by())

        root = _node('All', 'total')
        index = {}
        for r in rows:
            # A row is only placed as deep as it is actually named. A dump row
            # with an ASM but no head still belongs under its RSM.
            path, parent = [], root
            for lvl in levels:
                name = (r.get(lvl) or '').strip()
                if not name:
                    # Skipped, not stopped. The Pre-Sales Dump has RSM and ASM
                    # but no head, and breaking here left its every row
                    # unplaced — an empty tree over a full table.
                    continue
                path.append(name)
                key = tuple(path)
                node = index.get(key)
                if node is None:
                    node = _node(name, lvl)
                    node['depth'] = len(path) - 1
                    index[key] = node
                    parent['children'].append(node)
                parent = node

            # Figures are added at every level the row reaches, so a parent's
            # total is its own rows plus everything beneath it.
            touched, walk = [root], root
            for depth in range(len(path)):
                walk = index[tuple(path[:depth + 1])]
                touched.append(walk)
            for node in touched:
                node['revenue'] += float(r['revenue'] or 0)
                node['target'] += float(r['target'] or 0)
                node['quantity'] += float(r['quantity'] or 0)
                node['lines'] += r['lines'] or 0
                node['customers'] += r['customers'] or 0
                node['states'] += r['states'] or 0
                node['areas'] += r['areas'] or 0
                node['skus'] += r['skus'] or 0
                node['field_officers'] += r['field_officers'] or 0

        _finish(root)

        # A flat count per level, for the summary strip above the tree.
        per_level = []
        for lvl in levels:
            names = {n['name'] for n in index.values() if n['level'] == lvl}
            per_level.append({'level': lvl, 'count': len(names)})

        return Response({
            'levels': levels,
            'level_counts': per_level,
            'tree': root['children'],
            'totals': {k: root[k] for k in
                       ('revenue', 'target', 'achievement_pct', 'quantity',
                        'lines', 'customers', 'states', 'areas', 'skus',
                        'field_officers', 'reports', 'team')},
            'filters': applied,
        })
