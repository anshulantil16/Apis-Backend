"""SalesIQ views package.

    filters.py    the shared filter contract every endpoint accepts
    core.py       upload, template, KPIs, breakdowns, trend, forecast, export
    advanced.py   Pareto, quadrant, movers, anomalies, RFM, cohorts, pacing...
    auth.py       super-admin OTP login

Re-exported here so `from .views import X` in urls.py is unaffected by the split.
"""
from .filters import *    # noqa: F401,F403
from .core import *       # noqa: F401,F403
from .advanced import *   # noqa: F401,F403
from .auth import *       # noqa: F401,F403

# `import *` skips underscore names that tests and sibling modules rely on.
from .filters import (apply_filters, apply_dim_filters, DIMENSIONS, FILTERABLE,
                      _money, _pct_change, _period_bounds)
from .auth import SALESIQ_SUPER_ADMIN, _salesiq_allowed_emails
