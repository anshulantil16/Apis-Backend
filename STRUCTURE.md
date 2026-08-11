# Backend structure

Django project. **Each product is its own Django app** — an app owns its models,
its URL namespace and its business logic, and does not import from another
app's internals.

```
config/            settings, root urls, wsgi
  urls.py          mounts every app under /api/<app>/

pms/               PMS Simulator + Letters Generator      /api/pms/
sales/             SalesIQ sales analytics                /api/sales/
tada/              TA/DA portal                           /api/tada/
eom/               Employee of the Month                  /api/eom/
performance/       Performance hub                        /api/performance/
appraisal/         Appraisal hub                          /api/appraisal/
roompulse/         AdminPulse — room booking + admin item requests   /api/roompulse/
user_management/   Excel extractor tools (upload/export)  /api/user_management/
accounts/          registration                           /api/accounts/
```

**One Django app per product.** Before adding a new top-level directory, check this
table is still accurate — an app that stops being wired into `config/urls.py` and
`INSTALLED_APPS` becomes dead weight fast (this repo has had that happen twice:
`authentication`, `users` and `core` were all untouched `startapp` scaffolds with
no urls, no real models, and nothing importing them — removed in August 2026).
If you `startapp` something and don't finish wiring it up in the same change,
delete it rather than leaving it half-registered.

---

## pms/ — PMS Simulator + Letters Generator

Three separate systems share this app because they share the employee master.
They are kept in separate modules so it is obvious what controls what.

```
pms/
  models.py                PMSEmployee, OfferLetter, WarningLetter, batches
  urls.py                  every /api/pms/ route
  offer_letter.py          appraisal-letter PDF rendering + email
  warning_letter.py        warning-letter PDF rendering + email
  assets/                  logo, signature image
  migrations/

  views/                   (was one 2,700-line views.py)
    __init__.py            re-exports everything; urls.py is unaffected
    common.py              helpers used by MORE THAN ONE of the modules below
    auth.py                PMS Simulator OTP login
    simulator.py           employee master, scoring, increments, import/export
    offer_letters.py       appraisal / compensation-revision letter pipeline
    warning_letters.py     warning / disciplinary letter pipeline
```

**Where do I change...**

| Task | File |
|---|---|
| Increment / promotion / grade maths | `models.py` (computed properties) |
| What a field on the employee row does | `views/simulator.py` |
| Appraisal letter *wording or layout* | `offer_letter.py` |
| Appraisal letter *upload / batch / history* | `views/offer_letters.py` |
| Warning letter *wording or layout* | `warning_letter.py` |
| Warning letter *upload / form / history* | `views/warning_letters.py` |
| Who can log in to PMS | `views/auth.py` |

Rule for `views/common.py`: something belongs there **only** if two or more of
simulator / offer_letters / warning_letters use it. If one module uses it, it
lives in that module.

### CTC unit convention (easy to get wrong)

`current_ctc` and every recurring money field are stored **MONTHLY** and served
**ANNUAL** (`× CTC_ANNUAL_MULT`, see `views/common.py`). Anything written back
from the UI must be divided by 12 on the way in. One-time rewards and
percentages are never annualised.

---

## sales/ — SalesIQ

```
sales/
  models.py       SalesUpload, SalesRecord (denormalised fact table)
  urls.py         every /api/sales/ route
  ingest.py       Excel template + tolerant header/date/number parsing
  analytics.py    ALL the maths — Pareto, RFM, cohorts, pacing, seasonality...
  forecasting.py  Holt-Winters / Holt / drift, dependency-free
  views/
    __init__.py   re-exports
    filters.py    the shared filter contract every endpoint accepts
    core.py       upload, template, KPIs, breakdowns, trend, forecast, export
    advanced.py   thin HTTP wrappers over analytics.py
    auth.py       super-admin OTP login
```

**The maths lives in `analytics.py` and `forecasting.py`, never in views.**
Views only parse query params and shape the response. That separation is what
lets the formulas be unit-tested without going through a request, and there is
a 108-assertion audit that checks every one against hand-computed values.

**Where do I change...**

| Task | File |
|---|---|
| Accept a new column in the upload | `ingest.py` (`COLUMN_ALIASES`) |
| Add a dimension you can group by | `views/filters.py` (`DIMENSIONS`) |
| Change how a metric is calculated | `analytics.py` |
| Change the forecast model | `forecasting.py` |
| Add a new endpoint | `views/advanced.py` + `urls.py` |
| Who can log in to SalesIQ | `views/auth.py` |

---

## roompulse/ — AdminPulse (rooms + admin item requests)

Internal app/module name stayed `roompulse` when the product was rebranded and
expanded to "AdminPulse" — renaming a Django app means renaming a live
`app_label` and every `roompulse_*` table already in production, so the brand
name and the app name are allowed to diverge. Don't rename the app to match a
future brand change; just update user-facing text.

```
roompulse/
  models.py         Room, BookingRequest, ResourceRequest, Employee, AdminUser
  seed_data.py       SEED_ROOMS — source of truth for a fresh database
  status.py           room_status() / overlaps() / find_conflicts()
  ingest.py            employee directory Excel template + parsing
  urls.py               every /api/roompulse/ route
  views/
    auth.py             OTP login, resolve_role()
    perms.py            require_role() / actor_role() — re-resolves role from
                         email server-side; there is no server session, so a
                         client-sent role must NEVER be trusted
    rooms.py, bookings.py, resource_requests.py, employees.py, admins.py,
    analytics.py, reset.py
```

**Where do I change...**

| Task | File |
|---|---|
| Room / booking business logic | `status.py` |
| Add a resource-request category or urgency level | `models.py` (`CATEGORY_CHOICES` / `URGENCY_CHOICES`) |
| Who can log in / what role they get | `views/auth.py` |
| Any privileged action's permission check | `views/perms.py` |
| What a fresh/reset database looks like | `seed_data.py` (**and** `migrations/0002_seed_rooms.py`, kept in sync by hand) |

---

## Conventions

- **Portable ORM only.** Local dev is SQLite, production is MySQL. No window
  functions or vendor-specific date maths — they work in one and silently
  break in the other.
- **Ingest is forgiving, and says so.** Skipped rows, unreadable dates and
  unrecognised columns are reported back to the user, never dropped silently.
- **Long-running work goes to a background thread** with a batch row the UI
  polls (see `_process_offer_batch` / `_process_warning_batch`). Every counted
  failure must leave a `status='failed'` row, or the summary and the history
  screen will disagree.
- **Field truncation:** use `_clip_to_field()` before writing free text from a
  spreadsheet, so an over-long cell can't raise a DB "Data too long" error.
