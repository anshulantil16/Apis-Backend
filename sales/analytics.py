"""Advanced sales analytics.

Each function takes an already-filtered queryset and returns a plain dict the
API can hand straight to the client.

Two rules throughout:
  • Heavy lifting stays in the database (aggregate/values), the rest is done in
    Python over the already-collapsed groups — never over raw rows.
  • Only portable ORM features are used. Local dev is SQLite and production is
    MySQL, so anything relying on window functions or vendor date maths would
    work in one and silently break in the other.
"""
from datetime import date, timedelta
from django.db.models import Sum, Count, Min, Max, Avg


def _f(v):
    return float(v or 0)


def _pctc(cur, prev):
    """Growth %. None (not 0) when there's no baseline — "no prior data" and
    "flat" are different statements and must not render the same."""
    if not prev:
        return None
    return round((cur - prev) / prev * 100, 1)


def _add_months(d, n):
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def _months_between(a, b):
    return (b.year - a.year) * 12 + (b.month - a.month)


# ── Pareto / ABC ─────────────────────────────────────────────────────────
def pareto(qs, field, limit=400):
    """80/20 concentration. Classifies each group A (top 80% of revenue),
    B (next 15%) or C (last 5%) — the standard ABC inventory/customer split."""
    rows = (qs.exclude(**{field: ''}).values(field)
              .annotate(revenue=Sum('net_amount'))
              .order_by('-revenue')[:limit])
    rows = [{'name': r[field] or '—', 'revenue': _f(r['revenue'])} for r in rows]
    rows = [r for r in rows if r['revenue'] > 0]
    total = sum(r['revenue'] for r in rows)
    if not total:
        return {'results': [], 'total': 0, 'a_count': 0, 'b_count': 0, 'c_count': 0,
                'pareto_point': None}

    out, cum = [], 0.0
    a = b = c = 0
    pareto_point = None
    for i, r in enumerate(rows, 1):
        cum += r['revenue']
        share = cum / total * 100
        cls = 'A' if share <= 80 else ('B' if share <= 95 else 'C')
        if cls == 'A':
            a += 1
        elif cls == 'B':
            b += 1
        else:
            c += 1
        if pareto_point is None and share >= 80:
            pareto_point = {'count': i, 'pct_of_groups': round(i / len(rows) * 100, 1),
                            'pct_of_revenue': round(share, 1)}
        out.append({
            'name': r['name'], 'revenue': round(r['revenue'], 2),
            'share_pct': round(r['revenue'] / total * 100, 2),
            'cumulative_pct': round(share, 2), 'class': cls, 'rank': i,
        })
    return {'results': out, 'total': round(total, 2),
            'a_count': a, 'b_count': b, 'c_count': c, 'pareto_point': pareto_point}


# ── Growth quadrant (BCG-style) ──────────────────────────────────────────
def growth_matrix(qs_all, field, cur_from, cur_to, limit=60):
    """Plot each group on revenue (size) vs growth (momentum) against the
    preceding equal-length window, then quadrant it.

    Thresholds are the medians of the plotted set rather than fixed numbers,
    because "big" and "fast-growing" only mean anything relative to the rest
    of this particular book of business."""
    if not cur_from or not cur_to:
        return {'results': [], 'median_revenue': 0, 'median_growth': 0}

    span = (cur_to - cur_from).days + 1
    p_to = cur_from - timedelta(days=1)
    p_from = p_to - timedelta(days=span - 1)

    cur = {r[field]: _f(r['v']) for r in
           qs_all.filter(order_date__gte=cur_from, order_date__lte=cur_to)
                 .exclude(**{field: ''}).values(field).annotate(v=Sum('net_amount'))}
    prev = {r[field]: _f(r['v']) for r in
            qs_all.filter(order_date__gte=p_from, order_date__lte=p_to)
                  .exclude(**{field: ''}).values(field).annotate(v=Sum('net_amount'))}

    names = sorted(cur, key=lambda k: cur[k], reverse=True)[:limit]
    if not names:
        return {'results': [], 'median_revenue': 0, 'median_growth': 0}

    revs = sorted(cur[n] for n in names)
    med_rev = revs[len(revs) // 2]
    growths = [g for g in (_pctc(cur[n], prev.get(n, 0)) for n in names) if g is not None]
    med_growth = sorted(growths)[len(growths) // 2] if growths else 0.0

    out = []
    for n in names:
        g = _pctc(cur[n], prev.get(n, 0))
        big = cur[n] >= med_rev
        fast = (g if g is not None else 0) >= med_growth
        quad = ('star' if big and fast else 'cash_cow' if big else
                'rising' if fast else 'watch')
        out.append({
            'name': n, 'revenue': round(cur[n], 2),
            'prev_revenue': round(prev.get(n, 0), 2),
            'growth_pct': g, 'quadrant': quad,
            'is_new': prev.get(n, 0) == 0,
        })
    return {
        'results': out,
        'median_revenue': round(med_rev, 2), 'median_growth': round(med_growth, 1),
        'current_window': {'from': cur_from.isoformat(), 'to': cur_to.isoformat()},
        'prior_window': {'from': p_from.isoformat(), 'to': p_to.isoformat()},
        'legend': {
            'star': 'High revenue, growing — protect and invest',
            'cash_cow': 'High revenue, slowing — defend the base',
            'rising': 'Small but growing fast — worth backing',
            'watch': 'Small and slowing — review or exit',
        },
    }


# ── Top movers ───────────────────────────────────────────────────────────
def movers(qs_all, field, cur_from, cur_to, top=8):
    """Biggest absolute revenue gainers and losers vs the prior equal window.
    Ranked by absolute rupees rather than %, because a 400% jump on a tiny base
    is noise while a 5% slip on the biggest account is the real story."""
    if not cur_from or not cur_to:
        return {'gainers': [], 'losers': []}
    span = (cur_to - cur_from).days + 1
    p_to = cur_from - timedelta(days=1)
    p_from = p_to - timedelta(days=span - 1)

    cur = {r[field]: _f(r['v']) for r in
           qs_all.filter(order_date__gte=cur_from, order_date__lte=cur_to)
                 .exclude(**{field: ''}).values(field).annotate(v=Sum('net_amount'))}
    prev = {r[field]: _f(r['v']) for r in
            qs_all.filter(order_date__gte=p_from, order_date__lte=p_to)
                  .exclude(**{field: ''}).values(field).annotate(v=Sum('net_amount'))}

    rows = []
    for n in set(cur) | set(prev):
        c, p = cur.get(n, 0.0), prev.get(n, 0.0)
        rows.append({'name': n, 'revenue': round(c, 2), 'prev_revenue': round(p, 2),
                     'change': round(c - p, 2), 'growth_pct': _pctc(c, p),
                     'is_new': p == 0 and c > 0, 'is_lost': c == 0 and p > 0})
    rows.sort(key=lambda r: r['change'], reverse=True)
    return {
        'gainers': [r for r in rows if r['change'] > 0][:top],
        'losers': [r for r in rows if r['change'] < 0][-top:][::-1],
    }


# ── Anomaly detection ────────────────────────────────────────────────────
def anomalies(qs, z=2.0):
    """Flag months whose revenue sits more than `z` standard deviations from the
    series mean. Deliberately simple: with typical monthly history (12-36
    points) a robust z-score is as much as the data can honestly support."""
    rows = list(qs.values('period').annotate(v=Sum('net_amount')).order_by('period'))
    series = [(r['period'], _f(r['v'])) for r in rows]
    if len(series) < 4:
        return {'results': [], 'mean': 0, 'std': 0,
                'note': 'Need at least 4 months of history to detect anomalies.'}
    vals = [v for _, v in series]
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    std = var ** 0.5
    out = []
    for d, v in series:
        score = (v - mean) / std if std else 0.0
        if abs(score) >= z:
            out.append({
                'period': d.isoformat(), 'label': d.strftime('%b %Y'),
                'value': round(v, 2), 'z_score': round(score, 2),
                'direction': 'spike' if score > 0 else 'drop',
                'vs_mean_pct': round((v - mean) / mean * 100, 1) if mean else 0,
            })
    return {'results': out, 'mean': round(mean, 2), 'std': round(std, 2),
            'months': len(series),
            'note': f'{len(out)} month(s) outside {z} standard deviations.'}


# ── Seasonality profile ──────────────────────────────────────────────────
def seasonality(qs):
    """Average index per calendar month (100 = an average month). Needs at
    least two Januaries etc. to be meaningful, so the year count is returned
    alongside for the UI to caveat with."""
    rows = list(qs.values('period').annotate(v=Sum('net_amount')).order_by('period'))
    if not rows:
        return {'results': [], 'years': 0}
    buckets = {}
    years = set()
    for r in rows:
        d = r['period']
        buckets.setdefault(d.month, []).append(_f(r['v']))
        years.add(d.year)
    allv = [v for vs in buckets.values() for v in vs]
    grand = sum(allv) / len(allv) if allv else 0
    names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
             'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    out = []
    for m in range(1, 13):
        vs = buckets.get(m, [])
        avg = sum(vs) / len(vs) if vs else 0
        out.append({
            'month': m, 'label': names[m - 1],
            'avg_revenue': round(avg, 2),
            'index': round(avg / grand * 100, 1) if grand else 0,
            'samples': len(vs),
        })
    peak = max((o for o in out if o['samples']), key=lambda o: o['index'], default=None)
    low = min((o for o in out if o['samples']), key=lambda o: o['index'], default=None)
    return {'results': out, 'years': len(years), 'peak': peak, 'low': low,
            'reliable': len(years) >= 2}


# ── Heatmap: dimension x month ───────────────────────────────────────────
def heatmap(qs, field, top=12):
    """Matrix of the top groups against every month — makes it obvious when a
    single region/product carried or dragged a particular month."""
    top_names = [r[field] for r in
                 qs.exclude(**{field: ''}).values(field)
                   .annotate(v=Sum('net_amount')).order_by('-v')[:top]]
    if not top_names:
        return {'rows': [], 'periods': []}
    rows = (qs.filter(**{f'{field}__in': top_names})
              .values(field, 'period').annotate(v=Sum('net_amount')))
    periods = sorted({r['period'] for r in rows})
    grid = {n: {p: 0.0 for p in periods} for n in top_names}
    for r in rows:
        grid[r[field]][r['period']] = _f(r['v'])
    peak = max((v for n in grid for v in grid[n].values()), default=0) or 1
    return {
        'periods': [{'period': p.isoformat(), 'label': p.strftime('%b %y')} for p in periods],
        'rows': [{
            'name': n,
            'total': round(sum(grid[n].values()), 2),
            'cells': [{'period': p.isoformat(), 'value': round(grid[n][p], 2),
                       'intensity': round(grid[n][p] / peak, 3)} for p in periods],
        } for n in sorted(top_names, key=lambda n: -sum(grid[n].values()))],
        'peak': round(peak, 2),
    }


# ── RFM customer segmentation ────────────────────────────────────────────
_RFM_SEGMENTS = [
    # (label, description, predicate on (r, f, m) 1-5 scores)
    ('Champions', 'Bought recently, buy often, spend the most', lambda r, f, m: r >= 4 and f >= 4 and m >= 4),
    ('Loyal', 'Buy regularly and spend well', lambda r, f, m: r >= 3 and f >= 3 and m >= 3),
    ('Potential', 'Recent buyers with room to grow', lambda r, f, m: r >= 4 and f <= 2),
    ('New', 'Bought very recently, only once or twice', lambda r, f, m: r == 5 and f <= 2),
    ('At Risk', 'Used to buy often and big — going quiet', lambda r, f, m: r <= 2 and (f >= 3 or m >= 3)),
    ('Hibernating', 'Low value, long since last order', lambda r, f, m: r <= 2 and f <= 2),
]


def rfm(qs, max_customers=3000):
    """Recency / Frequency / Monetary scoring, quintile-ranked within this
    dataset. Recency is measured against the latest order in the data, not
    today, so an uploaded historical extract still segments sensibly."""
    rows = list(qs.exclude(customer_name='')
                  .values('customer_name')
                  .annotate(last=Max('order_date'), first=Min('order_date'),
                            freq=Count('invoice_no', distinct=True),
                            lines=Count('id'), monetary=Sum('net_amount'))
                  .order_by('-monetary')[:max_customers])
    if not rows:
        return {'results': [], 'segments': [], 'total_customers': 0,
                'note': 'No customer column in the uploaded data.'}

    as_of = max(r['last'] for r in rows)

    def score(values, reverse=False):
        """Rank -> 1..5, worst=1 best=5.

        Quintiles for n>=5. Below that a quintile split cannot reach 5 at all
        (with n=3 the top rank lands on 4), which would make the Champions
        segment unreachable — so small sets get an even linear spread that
        still pins the extremes at 1 and 5.
        """
        n = len(values)
        order = sorted(range(n), key=lambda i: values[i], reverse=reverse)
        out = [0] * n
        for rank, idx in enumerate(order):
            if n == 1:
                s = 3                                   # single customer: mid-band
            elif n < 5:
                s = round(rank / (n - 1) * 4) + 1
            else:
                s = min(5, int(rank * 5 / n) + 1)
            out[idx] = s
        return out

    rec_days = [(as_of - r['last']).days for r in rows]
    r_scores = score(rec_days, reverse=True)     # fewer days since order = better
    f_scores = score([r['freq'] for r in rows])
    m_scores = score([_f(r['monetary']) for r in rows])

    results, counts = [], {}
    for i, r in enumerate(rows):
        rs, fs, ms = r_scores[i], f_scores[i], m_scores[i]
        seg, desc = 'Hibernating', 'Low value, long since last order'
        for label, d, pred in _RFM_SEGMENTS:
            if pred(rs, fs, ms):
                seg, desc = label, d
                break
        counts.setdefault(seg, {'label': seg, 'description': desc,
                                'count': 0, 'revenue': 0.0})
        counts[seg]['count'] += 1
        counts[seg]['revenue'] += _f(r['monetary'])
        results.append({
            'customer': r['customer_name'],
            'recency_days': (as_of - r['last']).days,
            'frequency': r['freq'] or r['lines'],
            'monetary': round(_f(r['monetary']), 2),
            'r': rs, 'f': fs, 'm': ms, 'rfm': f'{rs}{fs}{ms}',
            'segment': seg,
            'first_order': r['first'].isoformat(),
            'last_order': r['last'].isoformat(),
        })

    segs = sorted(counts.values(), key=lambda s: -s['revenue'])
    for s in segs:
        s['revenue'] = round(s['revenue'], 2)
    return {'results': results, 'segments': segs, 'total_customers': len(results),
            'as_of': as_of.isoformat()}


# ── Cohort retention ─────────────────────────────────────────────────────
def cohorts(qs, max_months=12):
    """Group customers by the month of their first order, then track how many
    ordered again in each following month. Answers "do customers we win stick
    around?" — invisible in any total-revenue view."""
    rows = list(qs.exclude(customer_name='')
                  .values('customer_name', 'period')
                  .annotate(v=Sum('net_amount')))
    if not rows:
        return {'cohorts': [], 'note': 'No customer column in the uploaded data.'}

    first, active = {}, {}
    for r in rows:
        c, p = r['customer_name'], r['period']
        if c not in first or p < first[c]:
            first[c] = p
        active.setdefault(c, set()).add(p)

    groups = {}
    for c, f in first.items():
        groups.setdefault(f, []).append(c)

    out = []
    for f in sorted(groups):
        members = groups[f]
        size = len(members)
        cells = []
        for off in range(max_months):
            p = _add_months(f, off)
            n = sum(1 for c in members if p in active[c])
            cells.append({'offset': off, 'customers': n,
                          'pct': round(n / size * 100, 1) if size else 0})
        out.append({'cohort': f.isoformat(), 'label': f.strftime('%b %Y'),
                    'size': size, 'cells': cells})
    return {'cohorts': out[-18:], 'max_months': max_months}


# ── New vs repeat revenue ────────────────────────────────────────────────
def new_vs_repeat(qs):
    """Split each month's revenue into first-ever orders vs returning customers
    — separates real acquisition from churn masked by existing accounts."""
    rows = list(qs.exclude(customer_name='')
                  .values('customer_name', 'period').annotate(v=Sum('net_amount')))
    if not rows:
        return {'results': [], 'note': 'No customer column in the uploaded data.'}
    first = {}
    for r in rows:
        c, p = r['customer_name'], r['period']
        if c not in first or p < first[c]:
            first[c] = p
    agg = {}
    for r in rows:
        p = r['period']
        b = agg.setdefault(p, {'new': 0.0, 'repeat': 0.0, 'new_c': 0, 'repeat_c': 0})
        if first[r['customer_name']] == p:
            b['new'] += _f(r['v']); b['new_c'] += 1
        else:
            b['repeat'] += _f(r['v']); b['repeat_c'] += 1
    return {'results': [{
        'period': p.isoformat(), 'label': p.strftime('%b %Y'),
        'new': round(agg[p]['new'], 2), 'repeat': round(agg[p]['repeat'], 2),
        'new_customers': agg[p]['new_c'], 'repeat_customers': agg[p]['repeat_c'],
    } for p in sorted(agg)]}


# ── Year-on-year ─────────────────────────────────────────────────────────
def year_on_year(qs_all, dim_filtered_qs):
    """Per-month revenue grouped by calendar year, so the same month across
    years lines up. Uses the unfiltered-by-date queryset so prior years are
    still visible when the user has narrowed the date window."""
    rows = list(dim_filtered_qs.values('period').annotate(v=Sum('net_amount')).order_by('period'))
    if not rows:
        return {'years': [], 'results': []}
    by_year = {}
    for r in rows:
        d = r['period']
        by_year.setdefault(d.year, {})[d.month] = _f(r['v'])
    years = sorted(by_year)
    names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
             'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    results = []
    for m in range(1, 13):
        row = {'month': m, 'label': names[m - 1]}
        for y in years:
            row[str(y)] = round(by_year[y].get(m, 0), 2)
        results.append(row)
    totals = {str(y): round(sum(by_year[y].values()), 2) for y in years}
    growth = {}
    for i in range(1, len(years)):
        growth[str(years[i])] = _pctc(totals[str(years[i])], totals[str(years[i - 1])])
    return {'years': [str(y) for y in years], 'results': results,
            'totals': totals, 'growth': growth}


# ── Target pacing ────────────────────────────────────────────────────────
def pacing(qs):
    """Are we on track to hit target by the end of the period?

    Run-rate is computed on *elapsed* days rather than the whole window,
    because comparing a part-finished period against a full-period target
    always looks like failure and tells you nothing."""
    agg = qs.aggregate(rev=Sum('net_amount'), tgt=Sum('target_amount'),
                       lo=Min('order_date'), hi=Max('order_date'))
    rev, tgt = _f(agg['rev']), _f(agg['tgt'])
    lo, hi = agg['lo'], agg['hi']
    if not tgt or not lo or not hi:
        return {'has_target': False,
                'note': 'Add a Target column to the upload to enable pacing.'}

    # Period is assumed to run to the end of the month the data ends in.
    period_end = _add_months(hi.replace(day=1), 1) - timedelta(days=1)
    total_days = (period_end - lo).days + 1
    elapsed = (hi - lo).days + 1
    frac = min(1.0, elapsed / total_days) if total_days else 1.0

    run_rate = rev / elapsed if elapsed else 0
    projected = run_rate * total_days
    required = (tgt - rev) / max(1, (period_end - hi).days) if period_end > hi else 0

    return {
        'has_target': True,
        'revenue': round(rev, 2), 'target': round(tgt, 2),
        'achievement_pct': round(rev / tgt * 100, 1),
        'elapsed_pct': round(frac * 100, 1),
        'days_elapsed': elapsed, 'days_total': total_days,
        'days_remaining': max(0, (period_end - hi).days),
        'run_rate_per_day': round(run_rate, 2),
        'required_per_day': round(required, 2),
        'projected_total': round(projected, 2),
        'projected_vs_target_pct': round(projected / tgt * 100, 1),
        'on_track': projected >= tgt,
        'period_end': period_end.isoformat(),
        'gap': round(tgt - rev, 2),
    }


# ── Price & discount realisation ─────────────────────────────────────────
def price_realisation(qs):
    """Realised price per unit and discount intensity over time — separates
    growth that came from selling more from growth that came from charging
    more (or from buying share with discount)."""
    rows = list(qs.values('period')
                  .annotate(rev=Sum('net_amount'), qty=Sum('quantity'),
                            gross=Sum('gross_amount'), disc=Sum('discount'))
                  .order_by('period'))
    out = []
    for r in rows:
        qty, gross = _f(r['qty']), _f(r['gross'])
        out.append({
            'period': r['period'].isoformat(),
            'label': r['period'].strftime('%b %Y'),
            'revenue': round(_f(r['rev']), 2),
            'quantity': round(qty, 2),
            'avg_price': round(_f(r['rev']) / qty, 2) if qty else 0,
            'discount_pct': round(_f(r['disc']) / gross * 100, 1) if gross else 0,
        })
    verdict = None
    if len(out) >= 4:
        h = len(out) // 2
        early, late = out[:h], out[h:]
        ep = sum(o['avg_price'] for o in early) / len(early)
        lp = sum(o['avg_price'] for o in late) / len(late)
        eq = sum(o['quantity'] for o in early) / len(early)
        lq = sum(o['quantity'] for o in late) / len(late)
        dp, dq = _pctc(lp, ep), _pctc(lq, eq)
        if dp is not None and dq is not None:
            if dq > 2 and dp > 2:
                verdict = 'Growth is both volume- and price-led — the healthiest combination.'
            elif dq > 2 >= dp:
                verdict = 'Growth is volume-led; realised price is flat or falling.'
            elif dp > 2 >= dq:
                verdict = 'Growth is price-led; volumes are not expanding.'
            else:
                verdict = 'Both volume and realised price are soft.'
        return {'results': out, 'price_change_pct': dp, 'volume_change_pct': dq,
                'verdict': verdict}
    return {'results': out, 'verdict': verdict}
