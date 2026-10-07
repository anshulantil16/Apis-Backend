"""The selling organisation, and what each part of it sold.

Both primary-sales files carry the reporting line — HEAD above RSM above ASM —
but every other view flattens it, so you could rank ASMs against each other and
never see which RSM they answered to, or how many of them an RSM carries.

This assembles the tree in one query and rolls the figures up it. A level with
no name is dropped rather than shown as a blank branch: the Pre-Sales Dump has
RSM and ASM but no head, and an empty root labelled "" is noise, not structure.
"""
from django.db.models import Count, Max, Q, Sum
from rest_framework.response import Response
from rest_framework.views import APIView
from .auth import SalesIQAdminView, SalesIQView

from ..ingest import is_vacant
from .. import status as STATUS
from ..models import SalesRecord
from .filters import (apply_filters, detail_qs, count_people,
                      FINISHED_GOODS_PREFIX)


def _pct(part, whole):
    """Achievement %. None when there is no target — 0 would read as 'missed
    the plan', which is a different and untrue statement."""
    if not whole:
        return None
    return round((part / whole) * 100, 1)


def _node(name, level):
    return {
        'name': name, 'level': level, 'vacant': is_vacant(name),
        'revenue': 0.0, 'target': 0.0, 'quantity': 0.0, 'lines': 0,
        'customers': 0, 'states': 0, 'areas': 0, 'skus': 0, 'field_officers': 0,
        # How many invoice lines actually landed here. Without it a node
        # shows "0 customers" whether it genuinely sold to none or whether
        # the dump simply does not name this level -- and the second case
        # was every row on screen.
        'detail_lines': 0,
        # Collected rather than added. Summing per-group distinct counts
        # reported one head as covering 55 sub-regions when the company has
        # nothing like that many: each of his thirteen RSMs contributed its
        # own count and they were added together. Dropped before the
        # response is built.
        '_states': set(), '_areas': set(),
        'children': [],
    }


def _finish(node):
    """Sort children by revenue, roll counts up, and compute achievement."""
    for child in node['children']:
        _finish(child)
    if node['children']:
        node['children'].sort(key=lambda c: c['revenue'], reverse=True)
        node['reports'] = len(node['children'])
        node['team'] = sum(c.get('team', 0) or 1 for c in node['children'])
    else:
        node['reports'] = 0
        node['team'] = 0
    # A state two ASMs both sell in is ONE state the branch covers.
    node['states'] = len(node.pop('_states'))
    node['areas'] = len(node.pop('_areas'))
    node['achievement_pct'] = _pct(node['revenue'], node['target'])
    # One rule, so a branch here and the same name on a leaderboard cannot
    # come out different colours.
    node['status'] = STATUS.rag(node['achievement_pct'])
    node['revenue'] = round(node['revenue'], 2)
    node['target'] = round(node['target'], 2)
    return node


class SalesOrgView(SalesIQView):
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

        # Grouped by state and sub-region as well as by level, so coverage
        # can be collected as the set of places a branch reaches rather than
        # as a count that gets added to another count.
        rows = (qs.values(*levels, 'state', 'subzone')
                  .annotate(revenue=Sum('net_amount'), target=Sum('target_amount'),
                            lines=Count('id'))
                  .order_by())

        # Customers, SKUs and quantity are facts about an invoice, and the
        # review sheet -- which is what answers a month or a year, and so is
        # what `qs` holds most of the time -- has none of the three. Counted
        # off `qs` they came back as nought customers under every head, beside
        # a real revenue figure. They are read off the dump instead and merged
        # into the same tree, which is why a node's customer count can cover
        # fewer months than its revenue.
        detail = detail_qs(request)
        det_rows = (detail.values(*levels)
                    .annotate(quantity=Sum('quantity'), lines=Count('id'),
                              # Counted exactly as the headline tiles count
                              # them: a customer is a customer CODE, and an
                              # SKU is a finished-goods item code. Counted on
                              # the name instead, this panel reported 440
                              # customers beside a tile reading 454 -- the
                              # same question answered twice, differently.
                              customers=Count('customer_code', distinct=True,
                                              filter=~Q(customer_code='')),
                              skus=Count('sku', distinct=True,
                                         filter=Q(sku__istartswith=FINISHED_GOODS_PREFIX)))
                    .order_by())

        # Field officers are a headcount attached to a sub-region, not a
        # measure of a month. The review sheet writes the number once per
        # territory and the unpivot copies it onto all 24 of that territory's
        # month-rows, so adding the column up reported 380 officers as 9,120.
        # Counted here per territory instead, and each territory counted once
        # however many months of it are in the window.
        sfo_rows = (qs.exclude(subzone='')
                      .values(*levels, 'subzone')
                      .annotate(heads=Max('sfo_count'))
                      .order_by())

        root = _node('All', 'total')
        index = {}
        for r in rows:
            # A row that carries neither revenue nor plan does not get a
            # branch of its own.
            #
            # net_amount is the ELECTED actual: where the review sheet owns a
            # month, the dump's rows for that month are deliberately zeroed so
            # the same rupee is not counted from both files. Those zeroed rows
            # still name an RSM and an ASM, and the two files spell the
            # organisation differently, so every dump spelling the sheet does
            # not share was arriving as its own branch at Rs 0 -- sitting
            # beside colleagues at tens of crores with 73 customers written
            # underneath it. Read straight, that row said the man sold
            # nothing. What it meant was that his sales are reported on the
            # other file under another spelling.
            #
            # Their customers and SKUs are not lost: the pass below attaches
            # invoice detail to whichever branch does exist, and whatever
            # matches nothing lands on the root, where it still counts in the
            # totals. An install with no review sheet is unaffected -- its
            # dump rows carry real money, so they build the tree as before.
            if not (r['revenue'] or r['target']):
                continue
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
                node['lines'] += r['lines'] or 0
                if r.get('state'):
                    node['_states'].add(r['state'])
                if r.get('subzone'):
                    node['_areas'].add(r['subzone'])

        # The invoice-only figures, onto the tree the review sheet built --
        # and ONLY onto branches it already built.
        #
        # Creating a branch from a dump row produced a screen full of people
        # at nought. The two files spell the organisation differently, so an
        # ASM the dump names and the sheet does not became his own row, and
        # because net_amount is the ELECTED actual -- zero on a dump row
        # whose month the review sheet owns -- that row showed Rs 0 beside
        # colleagues at tens of crores, with 73 customers underneath it. Read
        # straight, it said this person sold nothing. What it meant was that
        # his sales are reported on the other file under another spelling.
        #
        # Unmatched detail is not dropped: it lands on the root, so the
        # customer and SKU totals stay whole, and `detail_reach` reports how
        # much of it never found a branch. A figure that cannot be attributed
        # belongs in the total and nowhere else.
        unplaced = 0
        for r in det_rows:
            path, parent = [], root
            for lvl in levels:
                name = (r.get(lvl) or '').strip()
                if not name:
                    continue
                node = index.get(tuple(path + [name]))
                if node is None:
                    # This spelling is not in the sheet's tree. Stop here
                    # rather than invent a branch for it.
                    break
                path.append(name)
                parent = node
            if not path:
                unplaced += r['lines'] or 0
            touched = [root] + [index[tuple(path[:d + 1])] for d in range(len(path))]
            for node in touched:
                node['quantity'] += float(r['quantity'] or 0)
                node['customers'] += r['customers'] or 0
                node['skus'] += r['skus'] or 0
                node['detail_lines'] += r['lines'] or 0

        # Roll the territory headcounts up the same tree, each territory
        # landing once on every level above it.
        seen = set()
        for r in sfo_rows:
            heads = int(r['heads'] or 0)
            if not heads:
                continue
            path = []
            for lvl in levels:
                name = (r.get(lvl) or '').strip()
                if name:
                    path.append(name)
            for depth in range(len(path)):
                key = tuple(path[:depth + 1])
                mark = (key, r['subzone'])
                if mark in seen:
                    continue
                seen.add(mark)
                node = index.get(key)
                if node is not None:
                    node['field_officers'] += heads
            mark = (('__root__',), r['subzone'])
            if mark not in seen:
                seen.add(mark)
                root['field_officers'] += heads

        # Counted once across the whole dump rather than added up per group:
        # one customer buying from two RSMs is one customer, and summing the
        # per-group distinct counts reported them twice at the top.
        whole = detail.aggregate(
            customers=Count('customer_code', distinct=True,
                            filter=~Q(customer_code='')),
            skus=Count('sku', distinct=True,
                       filter=Q(sku__istartswith=FINISHED_GOODS_PREFIX)))
        root['customers'] = whole['customers'] or 0
        root['skus'] = whole['skus'] or 0

        _finish(root)

        # A flat count per level, for the summary strip above the tree.
        #
        # Counted off the rows, not off the tree. The tree is keyed on names,
        # so it answers "how many branches" -- which is the same number only
        # for as long as nobody is spelled two ways and no two people share a
        # name. Both happen on this sheet, which is why it carries an APIS ID
        # and a BIZOM ID beside every people column, and why the strip above
        # the tree now counts those instead.
        per_level = [{'level': lvl, 'count': count_people([qs, detail], lvl)}
                     for lvl in levels]

        # Whether the invoice dump names these levels at all. Customers and
        # SKUs are invoice facts; if the dump carries no RSM column then
        # every node below the root has none of either, and a row of zeros
        # is a claim that nobody bought anything rather than an admission
        # that this file cannot say. The screen needs to tell the two apart.
        detail_total = detail.count()
        placed = sum(n['detail_lines'] for n in index.values() if n.get('depth') == 0)
        detail_reach = {
            'lines': detail_total,
            'placed': placed,
            # Invoice lines whose RSM or ASM is spelled in a way the review
            # sheet's tree does not contain. Their customers and SKUs are in
            # the totals; they are under nobody's name.
            'unplaced': unplaced,
            'levels_named': [lvl for lvl in levels
                             if detail.exclude(**{lvl: ''}).exists()],
        }

        # How the branches fall across the bands, counted at the TOP level
        # only -- the same person must not be counted once as an RSM and
        # again inside their own team. Reported so a screen can say "19 of 23
        # are under the red line" instead of leaving somebody to count
        # colours down a long list, and so that a wall of red reads as a
        # statement about how the plan was set rather than as nineteen
        # separate accusations.
        tally = STATUS.tally([n['achievement_pct'] for n in root['children']])
        tally['level'] = root['children'][0]['level'] if root['children'] else None

        return Response({
            'levels': levels,
            'level_counts': per_level,
            'detail_reach': detail_reach,
            'status_tally': tally,
            'status_thresholds': STATUS.thresholds(),
            'tree': root['children'],
            'totals': {k: root[k] for k in
                       ('revenue', 'target', 'achievement_pct', 'quantity',
                        'lines', 'customers', 'states', 'areas', 'skus',
                        'field_officers', 'reports', 'team')},
            'filters': applied,
        })
