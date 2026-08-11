# APIS Backend

Django REST Framework backend for the APIS internal tools suite — one Django
app per product (PMS/Letters Generator, SalesIQ, TA/DA, EOM, AdminPulse, and
more). See [STRUCTURE.md](STRUCTURE.md) for the full app-by-app map, endpoint
prefixes, and "where do I change X" tables.

## Setup

```bash
python -m venv venv
venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env       # fill in SECRET_KEY at minimum; DB settings are
                            # optional locally (falls back to SQLite)
python manage.py migrate
python manage.py runserver
```

API is served at `http://127.0.0.1:8000/api/<app>/...`.

## Database

- **Local dev**: SQLite (`db.sqlite3`), zero config needed.
- **QA / Production**: MySQL, configured via `DB_NAME`/`DB_USER`/`DB_PASSWORD`/
  `DB_HOST`/`DB_PORT` in `.env` (or `DATABASE_URL` if you prefer one string).
- Only use portable ORM features — no window functions or vendor-specific date
  functions. They'll pass on SQLite locally and break silently on MySQL.

## Branches & deployment

- `qa` — all day-to-day work happens here; deploys to the QA server.
- `main` — production. Only new tables/fields get deployed here; **existing
  production data and code are never touched directly.**
- QA server and deployment steps: ask whoever set up the QA box, or check the
  team's deployment notes — not duplicated here to avoid drift.

## Conventions

See the **Conventions** section at the bottom of
[STRUCTURE.md](STRUCTURE.md) — portable ORM, forgiving Excel ingest, and how
long-running batch jobs report status back to the UI.

Before adding a new Django app: wire it into `INSTALLED_APPS` and
`config/urls.py` in the same change, or don't commit it. Half-registered apps
that nothing imports are dead weight — this repo has already had to clean up
three of them (`authentication`, `users`, `core`).
