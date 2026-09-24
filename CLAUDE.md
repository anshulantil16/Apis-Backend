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

## The clock

`TIME_ZONE = 'Asia/Kolkata'`, `USE_TZ = True`. Datetimes are still stored in
UTC; what changed is what the server means by *now* and *today*.

Use `timezone.localtime()` / `timezone.localdate()`. **Never `datetime.now()`**
— that reads the host OS timezone, which on the QA server is UTC. The live
room grid did exactly that and ran 5.5 hours away from the times people had
typed in: a 10:00 meeting marked the room occupied at 15:30 IST.

## Freeing a room early

A room's status is derived from the clock on every request — nothing stores
"occupied", and a meeting frees its room by itself at its end time.

An admin can also end one early. `BookingRequest.released_at` records the
moment; `end_time` is left as booked, because the meeting did happen and for
how long it was booked is part of the record. Everything asking "is this room
in use?" goes through `status.effective_end()`, which is what makes a release
free the room both on the grid and for the next person trying to book that
slot. Cancelling is the different case — the meeting never happened.

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

## Goal Setting (`goalsetting/`) — roles are relative

**Nobody is "a manager" in the abstract — they are the manager OF someone.**
`EmployeeProfile.user_type` is a label from the upload sheet; it is not your
role on the sheet in front of you. On your own goal sheet you are the
employee, whatever your user_type says.

`views.plan_roles(actor_id, employee)` works out every role an actor holds in
relation to one sheet, and `acting_role(roles, status)` picks the one that
holds the pen at the current stage. Views take the role from those, never from
a `role` field in the request. Reading it off user_type is what stopped every
manager and HOD in the company from filling in their own goals: their own
draft was "with the employee" while they were "a manager".

Identity comes from `session.py` — a token minted at OTP verification and
returned in the `X-GoalSetting-Session` header. Before that, every endpoint
took the caller's word for who they were, and `/reset/`, which deletes every
goal sheet and every version of it, was open to anyone with the URL. Guard new
admin endpoints with `require_admin(request)`.

`services.route_after()` sends a hand-off past reviewers who are not really
there — no manager on file, an id naming nobody, someone who has left. Without
it a sheet lands at a stage nobody can act at and only the admin can free it.

## Tickets are a record

`SupportTicket.reviewed_by` / `reviewed_at` hold only the **most recent**
review. The trail lives in `TicketEvent` — append-only, one row per
transition, with actor and timestamp. Write to it through `_log()` in
`views/tickets.py` whenever a ticket's status changes; nothing updates or
deletes an event.

Not all work arrives as a ticket. `SupportTicket.origin` is `requested` or
`logged` — the second is a job IT or Admin did that nobody raised a ticket
for, recorded so the monthly count is the real one. It lives in the same table
so "what did IT do in September" stays one query; a report assembled from two
tables drifts the first time a field is added to one of them.

`performed_by_email` / `performed_on` are what a report groups by, set when a
ticket is closed and when a job is logged. `performed_on` is the day the work
happened, not the row's timestamp — closing a logged job must never move it,
or a Friday job written up on Monday lands in the wrong month.

`worktime.py` owns how long a job took and how much of it was outside office
hours (09:30–18:30, and all of Saturday and Sunday). After-hours time is
computed from the window worked, never claimed: "ninety minutes" reads the
same whether it was a Tuesday afternoon or 23:00–00:30 bringing a server back.
A job with no window recorded contributes nothing rather than counting as
zero — silent is not the same as inside hours.

Anything that counts tickets has to say which kind it means. Analytics counts
demand, so it excludes `logged`; the work report counts output, so it includes
both and reports the split.

Attachments are allowlisted by extension (`ALLOWED_ATTACHMENT_EXTS`) because
they are served back from `MEDIA_URL` — an `.html` or `.svg` attachment is a
script on our own origin.

## Analytics is windowed

`views/analytics.py` measures everything over `?days=` (default 30) and also
returns all-time `totals`. Anything added to the report needs both, or a quiet
period is indistinguishable from an empty system and the dashboard looks
broken. It covers bookings, item requests **and** tickets — the helpdesk was
missing from it at first, which is most of what people actually use.

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
