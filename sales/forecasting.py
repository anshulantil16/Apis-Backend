"""Sales forecasting — pure Python, no extra dependencies.

Deliberately dependency-free: statsmodels/prophet are not installed on the
servers and adding them for one endpoint is not worth the deployment risk.
Everything here is standard exponential smoothing implemented directly.

Method is chosen from how much history actually exists, because fitting a
seasonal model to eight months of data produces confident nonsense:

  >= 24 months  Holt-Winters (level + trend + multiplicative seasonality)
  >= 6  months  Holt's linear trend (level + trend, no seasonality)
  >= 2  months  drift from the average period-over-period change
  <  2  months  flat carry-forward of the last value

Every forecast returns a confidence band derived from the model's own fit
residuals, so a series the model tracks badly is visibly uncertain rather
than silently wrong.
"""
import math
from datetime import date


# ── what else is happening that month ────────────────────────────────────
#
# The model reads a seasonal index off the company's own history: it can say
# December runs 18% above an average month, but not why. This names what the
# business already knows sits in that month, so the two can be shown side by
# side -- the number from the data, the reason from the calendar.
#
# It is a calendar, not an input. Nothing here changes a single forecast
# figure; it is shown beside the figure so somebody asking "why is October
# high" gets an answer instead of a shrug. Several of these festivals move
# by a few weeks year to year, which is itself worth knowing when a month
# comes in off its index.
MONTH_CALENDAR = {
    1:  ['Makar Sankranti, Lohri, Pongal', 'Peak winter — honey demand runs high'],
    2:  ['Winter demand holding', 'Maha Shivratri in some years'],
    3:  ['Holi', 'Financial year end — channel loading into the trade'],
    4:  ['New financial year opens — trade stocks are low after March loading',
         'Navratri and Ram Navami'],
    5:  ['Summer peak', 'Wedding season'],
    6:  ['Monsoon onset', 'Schools reopen'],
    7:  ['Shravan begins — fasting demand for honey and dry fruit'],
    8:  ['Raksha Bandhan, Janmashtami, Independence Day', 'Gifting picks up'],
    9:  ['Ganesh Chaturthi, Onam', 'Festive stocking begins ahead of Q3'],
    10: ['Navratri, Dussehra, and Diwali in most years',
         'The largest gifting and stocking month of the year'],
    11: ['Diwali in some years, Bhai Dooj', 'Wedding season; winter begins'],
    12: ['Christmas and New Year', 'Winter — honey demand builds'],
}


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
