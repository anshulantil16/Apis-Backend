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
    """Multiplicative Holt-Winters. Multiplicative (not additive) because FMCG
    seasonal swings scale with volume — a festive uplift is "+30%", not
    "+X crore" regardless of base."""
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

    out = []
    for i in range(periods):
        s = seasonal[(len(series) + i) % season]
        out.append(_clamp0((level + (i + 1) * trend) * s))
    return out, fitted


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

    if n >= 24:
        method, label = _holt_winters(series, periods), 'holt-winters'
        fc, fitted = method
        note = 'Seasonal model — captures repeating month-of-year patterns.'
        confidence = 'high'
    elif n >= 6:
        fc, fitted = _holt(series, periods)
        label = 'holt-linear'
        note = ('Trend model. At least 24 months of history would enable seasonal '
                'forecasting (festive/summer cycles).')
        confidence = 'medium'
    elif n >= 2:
        fc, fitted = _drift(series, periods)
        label = 'drift'
        note = ('Very little history — this is a straight-line projection, not a '
                'real forecast. Treat as indicative only.')
        confidence = 'low'
    else:
        fc, fitted = [_clamp0(series[-1])] * periods, list(series)
        label = 'naive'
        note = 'Only one month of data — the last value is carried forward.'
        confidence = 'low'

    # Band from in-sample residuals. Widening with sqrt(horizon) reflects that
    # uncertainty compounds the further out you project.
    resid = [abs(a - f) for a, f in zip(series, fitted)]
    mae = _mean(resid) if resid else 0.0
    base = _mean(series) or 1.0
    mape = (mae / base) * 100 if base else 0.0

    out = []
    for i, v in enumerate(fc):
        spread = mae * 1.96 * math.sqrt(i + 1)
        out.append({
            'period': _add_months(last_date, i + 1).isoformat(),
            'value': round(v, 2),
            'lower': round(_clamp0(v - spread), 2),
            'upper': round(v + spread, 2),
        })

    return {
        'method': label,
        'confidence': confidence,
        'note': note,
        'history_months': n,
        'mae': round(mae, 2),
        'mape': round(mape, 1),
        'points': out,
        'forecast_total': round(sum(p['value'] for p in out), 2),
    }
