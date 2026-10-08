"""Sales forecasting — pure Python, no extra dependencies.

Deliberately dependency-free: statsmodels/prophet are not installed on the
servers and adding them for one endpoint is not worth the deployment risk.
Everything here is standard exponential smoothing implemented directly.

Method is chosen from what is actually available, because fitting a
seasonal model to eight months of data produces confident nonsense:

  AOP loaded     run rate against plan -- the plan carries the month shape,
                 the business's own achievement rate carries the level
  >= 24 months   Holt-Winters (level + trend + multiplicative seasonality)
  >= 6  months   Holt's linear trend (level + trend, no seasonality)
  >= 2  months   drift from the average period-over-period change
  <  2  months   flat carry-forward of the last value

The plan-anchored method is preferred wherever the plan reaches, and the
statistical models below it are the fallback for when it does not. The
reason is that a pure extrapolation has nothing holding it down: it reads
the slope of the months it was given and keeps going, so a strong first
half projects a second half the business never planned for and nobody in
the review recognises. The AOP is the company's own month-by-month shape
for the year -- built from the sales plan, the launch calendar and the
festive quarter -- and it is a far better carrier of that shape than a
slope fitted to a handful of months. What the model still has to supply is
the LEVEL: plans are not met exactly, and the honest question is what rate
this business has actually been running at against its own plan.

Every forecast returns a confidence band derived from the model's own fit
residuals, so a series the model tracks badly is visibly uncertain rather
than silently wrong.
"""
import math
from datetime import date


# ── what else is happening that month ────────────────────────────────────
#
# The model reads a seasonal index off the company's own history: it can say
# December runs 18% above an average month, but not why. This names what else
# falls in that month, so the two sit side by side -- the size of the effect
# from APIS's own data, the calendar from the calendar.
#
# What is in here, and what is deliberately NOT.
#
# Only dated events: festivals, the financial year boundary, the monsoon.
# Those are facts about the Indian calendar and anyone can check them.
#
# What was here first, and has been taken out, were statements about this
# business -- "peak winter, honey demand runs high", "the largest gifting and
# stocking month", "channel loading into the trade". They were written from
# general knowledge of Indian FMCG, not from anything APIS has said or any
# figure in these files, and shown beside a real forecast they would have
# read as established fact about this company. If December runs hot for APIS,
# the seasonal index says so out of their own invoices, and that is a claim
# worth something. The same sentence from me is worth nothing and is harder
# to argue with, which is the worse combination.
#
# So: the data says how big, the calendar says what else was on, and nobody
# asserts why except the people who actually know. SALESIQ_MONTH_CALENDAR in
# settings replaces any month, for when they want to write that down.
MONTH_CALENDAR = {
    1:  ['Makar Sankranti, Lohri, Pongal', 'Republic Day'],
    2:  ['Maha Shivratri, in some years'],
    3:  ['Holi', 'Financial year ends'],
    4:  ['Financial year opens', 'Navratri, Ram Navami, Baisakhi'],
    5:  ['Peak summer'],
    6:  ['Monsoon onset', 'Schools reopen'],
    7:  ['Shravan begins, in most years'],
    8:  ['Raksha Bandhan, Janmashtami', 'Independence Day'],
    9:  ['Ganesh Chaturthi, Onam'],
    10: ['Navratri and Dussehra', 'Diwali, in most years'],
    11: ['Diwali, in some years', 'Bhai Dooj'],
    12: ['Christmas, New Year'],
}

try:                                        # pragma: no cover - settings shim
    from django.conf import settings as _settings
    MONTH_CALENDAR = {**MONTH_CALENDAR,
                      **{int(k): v for k, v in
                         getattr(_settings, 'SALESIQ_MONTH_CALENDAR', {}).items()}}
except Exception:
    pass


# ── helpers ──────────────────────────────────────────────────────────────
def _add_months(d, n):
    """First-of-month `n` months after date `d`."""
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _clamp0(v):
    """Sales can't be negative — a trending-down model must not predict below 0."""
    return v if v > 0 else 0.0


# ── models ───────────────────────────────────────────────────────────────
def _holt(series, periods, alpha=0.5, beta=0.3):
    """Holt's linear trend. Returns (forecast list, fitted list)."""
    level, trend = series[0], series[1] - series[0]
    fitted = [series[0]]
    for v in series[1:]:
        fitted.append(level + trend)
        last_level = level
        level = alpha * v + (1 - alpha) * (level + trend)
        trend = beta * (level - last_level) + (1 - beta) * trend
    return [_clamp0(level + (i + 1) * trend) for i in range(periods)], fitted


def _holt_winters(series, periods, season=12, alpha=0.4, beta=0.15, gamma=0.3):
    """Multiplicative Holt-Winters.

    Multiplicative (not additive) because FMCG seasonal swings scale with
    volume — a festive uplift is "+30%", not "+X crore" regardless of base.

    Returns (forecast, fitted, seasonal index per forecast month).
    """
    n_seasons = len(series) // season
    # Seasonal indices seeded from per-season averages against the grand mean.
    grand = _mean(series[:n_seasons * season]) or 1.0
    seasonal = []
    for i in range(season):
        pts = [series[j * season + i] for j in range(n_seasons)]
        seasonal.append((_mean(pts) / grand) if grand else 1.0)
    # A zero index would make the multiplicative update collapse to zero forever.
    seasonal = [s if s > 0.05 else 0.05 for s in seasonal]

    level = _mean(series[:season])
    trend = (_mean(series[season:season * 2]) - level) / season if n_seasons >= 2 else 0.0

    fitted = []
    for i, v in enumerate(series):
        s = seasonal[i % season]
        fitted.append((level + trend) * s)
        last_level = level
        level = alpha * (v / s) + (1 - alpha) * (level + trend)
        trend = beta * (level - last_level) + (1 - beta) * trend
        seasonal[i % season] = gamma * (v / level if level else 1.0) + (1 - gamma) * s

    out, indices = [], []
    for i in range(periods):
        s = seasonal[(len(series) + i) % season]
        out.append(_clamp0((level + (i + 1) * trend) * s))
        # Kept, not discarded: this multiplier IS the answer to "why is
        # December higher than November". It is read off the company's own
        # history, so it can be shown beside the number it produced.
        indices.append(round(s, 3))
    return out, fitted, indices


def _drift(series, periods):
    """Average period-over-period change carried forward."""
    steps = [series[i] - series[i - 1] for i in range(1, len(series))]
    step = _mean(steps)
    last = series[-1]
    return [_clamp0(last + step * (i + 1)) for i in range(periods)], list(series)


def _stdev(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


# How hard to favour recent months when reading the run rate. 0.85 means a
# month counts about 15% less than the one after it, so half the weight sits
# in the last four or five months: recent enough to follow a business that
# has picked up or slowed, long enough not to swing on one odd month.
RUN_RATE_DECAY = 0.85

# Below this many months with BOTH an actual and a plan there is no rate to
# read -- three points is already thin, and two would make the forecast an
# echo of whichever month happened to be last.
MIN_OVERLAP = 3


def _median(xs):
    if not xs:
        return 0.0
    ys = sorted(xs)
    n = len(ys)
    return ys[n // 2] if n % 2 else (ys[n // 2 - 1] + ys[n // 2]) / 2


# How wide the high/low lines may ever be, as a fraction of plan. Both ends
# matter and both are judgements, so both are settable.
#
# The floor stops a business that has tracked its plan closely for a few
# months being shown a hairline, as though the rest of the year were
# settled. The ceiling stops the opposite: one erratic pair of months used
# to produce a range of 41% to 170% of plan, which is not a forecast but a
# shrug with an axis on it. A range nobody can act on is worse than a range
# that admits where it was truncated, so when it is truncated the screen
# says so.
BAND_FLOOR = 0.05
BAND_CEILING = 0.25


def _band_limits():
    lo, hi = BAND_FLOOR, BAND_CEILING
    try:
        from django.conf import settings
        lo = float(getattr(settings, 'SALESIQ_BAND_FLOOR', lo))
        hi = float(getattr(settings, 'SALESIQ_BAND_CEILING', hi))
    except Exception:
        pass
    return lo, hi


def run_rate_against_plan(overlap):
    """-> (rate, half_width, [(month, ratio)], capped) from months with both.

    The rate is what the business has been achieving against its own plan,
    recent months weighted heaviest, and it is a RATIO OF SUMS rather than a
    mean of monthly ratios. That is not a refinement, it is a correction. A
    month carrying a small plan and an ordinary month's invoicing produces a
    ratio of three or four, and averaged in beside the rest it dragged the
    whole rate up on the strength of the month that mattered least. Summing
    the money on each side first gives every rupee one vote instead of
    giving every month one vote, which is also exactly what the business
    reads off its own sheet as YTD ACH over YTD AOP.

    The half width of the band is a ROBUST measure of how far the monthly
    ratios sit from that rate -- the median absolute deviation, scaled to
    read like a standard deviation. The mean-based version was moved by the
    very months it should have ignored: a financial year opens with a full
    month of plan against a fraction of a month of invoicing, and those
    opening months are not evidence about November. The median does not care
    how extreme an outlier is, only that it is on one side.

    A month with no plan, or a plan of zero, is skipped rather than counted
    as a miss: there is no rate to read off a month nobody set a target for.
    """
    rows = [(m, a, t) for m, a, t in overlap if t > 0]
    if not rows:
        return None, 0.0, [], False
    ratios = [(m, a / t) for m, a, t in rows]

    weights = [RUN_RATE_DECAY ** i for i in range(len(rows) - 1, -1, -1)]
    earned = sum(w * a for w, (_, a, _) in zip(weights, rows))
    planned = sum(w * t for w, (_, _, t) in zip(weights, rows))
    if planned <= 0:
        return None, 0.0, [], False
    rate = earned / planned

    # 1.4826 is the constant that makes a median absolute deviation match a
    # standard deviation on normally distributed data, so the 1.96 below
    # still means what it usually means.
    mad = _median([abs(r - rate) for _, r in ratios]) * 1.4826
    raw = 1.96 * mad
    lo, hi = _band_limits()
    half = max(lo, min(hi, raw))
    return rate, half, ratios, raw > hi


def _plan_anchored(plan_months, rate, half):
    """-> [(month, value, lower, upper)] for each planned month.

    Every figure is that month's AOP scaled by the run rate, so the forecast
    keeps the plan's own shape -- its festive quarter, its launches, its
    quiet months -- and only moves the level to where the business has
    actually been running.

    The band is the same AOP scaled by the rate's own spread, bounded at
    both ends. It does NOT widen with the horizon the way an extrapolated
    trend's band does, and that is deliberate: the uncertainty here is "what
    rate will we run at", which is a level, not a random walk compounding
    month on month. Widening it would borrow a correction from a model that
    is not being used.
    """
    lo_rate = max(0.0, rate - half)
    hi_rate = rate + half
    return [(m, _clamp0(t * rate), _clamp0(t * lo_rate), _clamp0(t * hi_rate))
            for m, t in plan_months]


# ── public API ───────────────────────────────────────────────────────────
def forecast_series(points, periods=6):
    """Forecast a monthly series.

    `points`: chronological list of (date-of-month-start, value).
    Returns a dict the API can return as-is.
    """
    points = [(d, float(v or 0)) for d, v in points if d is not None]
    points.sort(key=lambda p: p[0])

    if not points:
        return {'method': 'none', 'confidence': 'none', 'points': [],
                'history_months': 0,
                'note': 'No data available to forecast from.'}

    series = [v for _, v in points]
    last_date = points[-1][0]
    n = len(series)

    indices = None
    if n >= 24:
        fc, fitted, indices = _holt_winters(series, periods)
        label = 'holt-winters'
        note = 'Seasonal model — captures repeating month-of-year patterns.'
        confidence = 'high'
        spec = {
            'name': 'Holt-Winters (triple exponential smoothing)',
            'why': (f'{n} months of history is enough to watch a month-of-year '
                    f'pattern repeat, so the model is allowed to use one.'),
            'reads': [
                'Level — where sales are now, recent months weighted heaviest',
                'Trend — which way the level has been moving, month on month',
                'Seasonality — how each month of the year compares with an '
                'average month, measured across the whole history',
            ],
            'params': {'alpha': 0.4, 'beta': 0.15, 'gamma': 0.3, 'season': 12},
            'seasonality': 'multiplicative',
            'seasonality_why': (
                'A festive uplift in FMCG is "+30%", not "+X crore" whatever '
                'the base, so the seasonal effect multiplies the level rather '
                'than being added to it.'),
        }
    elif n >= 6:
        fc, fitted = _holt(series, periods)
        label = 'holt-linear'
        note = ('Trend model. At least 24 months of history would enable seasonal '
                'forecasting (festive/summer cycles).')
        confidence = 'medium'
        spec = {
            'name': "Holt's linear trend (double exponential smoothing)",
            'why': (f'{n} months of history shows a direction but not yet a '
                    f'repeating yearly shape. Fitting seasonality to this much '
                    f'data produces confident nonsense, so it is not fitted.'),
            'reads': [
                'Level — where sales are now, recent months weighted heaviest',
                'Trend — which way the level has been moving, month on month',
            ],
            'params': {'alpha': 0.5, 'beta': 0.3},
            'seasonality': 'none',
            'seasonality_why': ('Needs 24 months. Until then every month is '
                                'projected off the trend alone, so a festive '
                                'month and a quiet one come out alike.'),
        }
    elif n >= 2:
        fc, fitted = _drift(series, periods)
        label = 'drift'
        note = ('Very little history — this is a straight-line projection, not a '
                'real forecast. Treat as indicative only.')
        confidence = 'low'
        spec = {
            'name': 'Drift (straight-line projection)',
            'why': (f'Only {n} months of history. This is arithmetic, not a '
                    f'forecast: the average month-on-month change carried '
                    f'forward.'),
            'reads': ['The average change from one month to the next'],
            'params': {},
            'seasonality': 'none',
            'seasonality_why': 'Needs 24 months of history.',
        }
    else:
        fc, fitted = [_clamp0(series[-1])] * periods, list(series)
        label = 'naive'
        note = 'Only one month of data — the last value is carried forward.'
        confidence = 'low'
        spec = {
            'name': 'Last value carried forward',
            'why': 'One month of history. There is nothing to fit a model to.',
            'reads': ['The most recent month'],
            'params': {},
            'seasonality': 'none',
            'seasonality_why': 'Needs 24 months of history.',
        }

    # ── Confidence band ──────────────────────────────────────────────────
    # Built from in-sample residuals, with two corrections that matter:
    #
    # 1. MAE is not a standard deviation. For normally distributed errors
    #    sigma = MAE * sqrt(pi/2) ~= 1.2533 * MAE. Using MAE directly as sigma
    #    understates the interval by ~25%.
    # 2. A floor of 5% of the series mean. A model can fit its own history
    #    almost perfectly (a clean seasonal series drives MAE to ~0), but that
    #    is not evidence the future is certain — an unfloored band would render
    #    as a hairline and imply precision no sales forecast has.
    #
    # sqrt(horizon) widening reflects uncertainty compounding as you project
    # further out (the random-walk variance result).
    resid = [abs(a - f) for a, f in zip(series, fitted)]
    mae = _mean(resid) if resid else 0.0
    base = _mean(series) or 1.0
    mape = (mae / base) * 100 if base else 0.0

    sigma = max(mae * 1.2533, base * 0.05)

    out = []
    for i, v in enumerate(fc):
        spread = sigma * 1.96 * math.sqrt(i + 1)
        month = _add_months(last_date, i + 1)
        point = {
            'period': month.isoformat(),
            'value': round(v, 2),
            'lower': round(_clamp0(v - spread), 2),
            'upper': round(v + spread, 2),
        }
        # Why this month and not the one beside it. The index is the model's
        # own reading of the company's history; the calendar names what the
        # business already knows sits in that month.
        if indices is not None:
            point['seasonal_index'] = indices[i]
            point['seasonal_pct'] = round((indices[i] - 1) * 100, 1)
        point['calendar'] = MONTH_CALENDAR.get(month.month, [])
        out.append(point)

    spec['band'] = (
        f'A 95% interval, built from how far this model missed on the months '
        f'it has already seen — Rs {mae:,.0f} on average, {mape:.1f}% of a '
        f'typical month. It widens the further out the forecast reaches, '
        f'because an error in month one is carried into month two.')
    spec['floor'] = (
        'The band never narrows below 5% of an average month, however well '
        'the model fits its own history. Fitting the past tightly is not '
        'evidence the future is certain.')
    spec['history_months'] = n

    return {
        'method': label,
        'confidence': confidence,
        'note': note,
        'history_months': n,
        'mae': round(mae, 2),
        'mape': round(mape, 1),
        'spec': spec,
        'points': out,
        'forecast_total': round(sum(p['value'] for p in out), 2),
    }


def forecast_from_plan(points, plan, periods=6):
    """Forecast the months the AOP reaches, anchored to it.

    `points`: chronological [(month, actual)] -- the history.
    `plan`:   {month: AOP} for every month the plan covers, past and future.

    Returns the same shape as forecast_series(), or None when there is not
    enough overlap between the two to read a run rate -- in which case the
    caller falls back to the statistical models.
    """
    points = [(d, float(v or 0)) for d, v in points if d is not None]
    points.sort(key=lambda p: p[0])
    if not points or not plan:
        return None

    last = points[-1][0]
    overlap = [(d, v, plan[d]) for d, v in points if d in plan and plan[d] > 0]
    if len(overlap) < MIN_OVERLAP:
        return None

    ahead = sorted(m for m in plan if m > last and plan[m] > 0)[:periods]
    if not ahead:
        return None

    rate, half, ratios, capped = run_rate_against_plan(overlap)
    if rate is None:
        return None

    rows = _plan_anchored([(m, plan[m]) for m in ahead], rate, half)

    out = []
    for m, v, lo, hi in rows:
        out.append({
            'period': m.isoformat(),
            'value': round(v, 2),
            'lower': round(lo, 2),
            'upper': round(hi, 2),
            'aop': round(plan[m], 2),
            'calendar': MONTH_CALENDAR.get(m.month, []),
        })

    # How well the rate describes the months it was read from: the average
    # distance between what the plan said times the rate, and what actually
    # happened. It is the same quantity the statistical models report as
    # MAE, measured the same way, so the two are comparable on screen.
    resid = [abs(a - t * rate) for _, a, t in overlap]
    mae = _mean(resid)
    base = _mean([a for _, a, _ in overlap]) or 1.0

    worst = min(ratios, key=lambda r: r[1])
    best = max(ratios, key=lambda r: r[1])

    return {
        'method': 'plan-anchored',
        'confidence': 'high' if len(overlap) >= 6 else 'medium',
        'note': ('Built on the AOP: the plan sets each month\'s shape, and the '
                 'rate this business has been running at against it sets the '
                 'level.'),
        'history_months': len(points),
        'mae': round(mae, 2),
        'mape': round((mae / base) * 100, 1),
        'points': out,
        'forecast_total': round(sum(p['value'] for p in out), 2),
        'run_rate': {
            'rate_pct': round(rate * 100, 1),
            'spread_pct': round(half * 100, 1),
            'low_pct': round(max(0.0, rate - half) * 100, 1),
            'high_pct': round((rate + half) * 100, 1),
            'capped': capped,
            'months': len(overlap),
            'best': {'month': best[0].strftime('%b %Y'), 'pct': round(best[1] * 100, 1)},
            'worst': {'month': worst[0].strftime('%b %Y'), 'pct': round(worst[1] * 100, 1)},
            'by_month': [{'month': m.strftime('%b %Y'), 'pct': round(r * 100, 1)}
                         for m, r in ratios],
        },
        # Short lines, not paragraphs. This panel exists to be scanned by
        # somebody being asked a question in a review, and a wall of prose is
        # the one shape that cannot be scanned.
        'spec': {
            'name': 'Run rate against AOP',
            'why': [
                'The AOP is the company\'s own month-by-month shape for the year.',
                'It already carries the festive quarter and the launch calendar.',
                'A slope fitted to a few months carries none of that, and nothing '
                'holds it down — it keeps going, so a strong first half projects '
                'a second half nobody planned for.',
            ],
            'reads': [
                'The AOP for each month ahead, as uploaded.',
                f'What was achieved against plan across {len(overlap)} months '
                f'that have both figures.',
                'Recent months count for more, so the rate follows a business '
                'that has picked up or slowed.',
                'Money is summed on each side before dividing — every rupee gets '
                'one vote, not every month.',
            ],
            'params': {'decay': RUN_RATE_DECAY, 'min_overlap': MIN_OVERLAP},
            'seasonality': 'from the AOP',
            'seasonality_why': [
                'Nothing is fitted from history.',
                'The month-on-month shape is the plan\'s own.',
            ],
            'band': [
                f'The same AOP, scaled between '
                f'{round(max(0.0, rate - half) * 100, 1)}% and '
                f'{round((rate + half) * 100, 1)}% of plan.',
                'Built on how far the monthly rates sit from the rate above, '
                'measured by the median rather than the mean — so the opening '
                'months of a year cannot stretch it on their own.',
                'It does not widen further out: this is uncertainty about a '
                'level, not an error compounding month on month.',
            ],
            'floor': [
                f'Never narrower than {round(_band_limits()[0] * 100)}% of plan — '
                f'a few months on track does not settle the year.',
                f'Never wider than {round(_band_limits()[1] * 100)}% either, '
                f'because a range nobody can act on says nothing.'
                + (' This one is at that limit.' if capped else ''),
            ],
            'history_months': len(points),
        },
    }
