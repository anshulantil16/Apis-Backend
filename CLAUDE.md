# APIS Intranet — Backend

Django REST backend for the APIS India intranet. Two people work on this repo
(Anshul and Rainy) from separate laptops, through separate Claude sessions.
This file is how those sessions stay in agreement, because it travels with git
and each machine's Claude memory does not.

**Keep it current.** When a change alters a contract other code depends on —
an auth scheme, a shared helper, a field everything reads — record it here in
the same commit. The other person's session has no other way to learn it.

## Branches

`dev_anshul`, `qa` and `dev_rainy` are kept **identical**. Work lands on your
own branch and is then fast-forwarded onto the other two, so all three always
point at the same commit.

Both of us commit as `anshulantil16 <anshul@apisindia.com>`, so **git
authorship says nothing about who wrote a change** — the branch it arrived on
does.

## Running things

```bash
./venv/Scripts/python.exe manage.py test          # all of it
./venv/Scripts/python.exe manage.py test roompulse
```

Never `python3 manage.py` — the system Python has no Django.

## Migrations

Generate and commit them locally. **Never run `makemigrations` on a server:**
it writes files that are not in git and the next deploy conflicts with them.

`DEFAULT_AUTO_FIELD` is unset, so Django keeps proposing `id` field
alterations on `pms` and `roompulse`. That drift predates this work and is
deliberately left alone — do not bundle it into an unrelated deploy.

## AdminPulse (`roompulse/`) — identity

**An `email` in a request body is data about the request. It is never a claim
about who is making it.**

Sign-in mints a token (`auth.issue_session`) held in the database cache; the
client returns it in the `X-AdminPulse-Session` header, and `perms.actor_role`
resolves the caller from that alone.

It did not always work this way. Every endpoint used to read an `email` out of
the request and believe it, so the OTP bought nothing: anyone could approve
tickets, grant themselves Admin, or call `/reset/` — which deletes every
ticket, booking and employee — by sending the super admin's address, which is
a constant in `views/auth.py`.

When adding an AdminPulse endpoint:

- `require_signed_in(request)` for anything that reads someone's data
- `require_role(request, 'it_support', 'super_admin')` for privileged actions
- take the actor from `actor_role(request)`, never from `request.data`
- an employee sees only their own rows; only staff roles see everyone's

## Tickets are a record

`SupportTicket.reviewed_by` / `reviewed_at` hold only the **most recent**
review. The trail lives in `TicketEvent` — append-only, one row per
transition, with actor and timestamp. Write to it through `_log()` in
`views/tickets.py` whenever a ticket's status changes; nothing updates or
deletes an event.

Attachments are allowlisted by extension (`ALLOWED_ATTACHMENT_EXTS`) because
they are served back from `MEDIA_URL` — an `.html` or `.svg` attachment is a
script on our own origin.

## SalesIQ (`sales/`) — the two primary files

`Primary sales data.xlsx` has two sheets that describe the same business
differently, and the difference matters:

- **PRI SALES DUMP** — live ERP extract, **rupees**, line level, all channels.
  Only the month it was taken in, plus trailing credit notes.
- **YTD,AOP vs.ACH** — monthly review sheet, **lakhs**, general trade only,
  but two full financial years.

So: revenue is elected **per month** (`models.sync_actual_source`), the review
sheet's figures are scaled by 100,000, and achievement is compared only over
months that have both a plan and a result (`filters.comparable_window`). The
financial year runs **April to March**.

`measured_amount` is each row's own figure and is never overwritten;
`net_amount` is the elected one and is zero when the other source owns that
month.

Sanity check against the real file: revenue ₹274 Cr, plan ₹319 Cr, achievement
73%. An achievement in the thousands of percent means the lakhs scaling broke.

## Servers

QA only. **Never touch PROD** — it is live with real users.

- QA: `http://103.205.66.45:8080`, code at `/var/www/html/apis-qa/backend`
- Gunicorn is a systemd service: `sudo systemctl restart apis-gunicorn-qa.service`
- git on the server needs `sudo`
- QA is MySQL, not SQLite

Deploy steps are in the shared deployment notes; pull and build as **separate
commands**, never chained with `&&` — a failed fetch silently skips the rest
while everything downstream still looks fine.
