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

**Local is SQLite, QA and Live are MySQL, and they disagree about what is an
error.** A green test run here is not evidence a migration will apply there.
The ones that bite:

- **Narrowing a column** fails on MySQL if any existing row would be
  truncated — `(1265, "Data truncated for column ...")`. SQLite applies the
  same `ALTER` silently and keeps the long values. Clear or shorten the data
  in an earlier migration, then change the schema in the next one. Ordering
  these the other way round is what broke a QA deploy of
  `noticeboard.NewsItem.external_id`.
- **Indexed columns** cannot exceed MySQL's utf8mb4 key limit of 3072 bytes,
  i.e. 768 characters. SQLite has no such limit. If a value must be long and
  looked up, store a hash and index that.
- **Widening is always safe**; it is only shrinking that needs the two-step.

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

## Help Desk (`roompulse/`) — identity

**The product is called Help Desk.** It was AdminPulse, and before that
RoomPulse. Every string a user reads — including the OTP email — says Help
Desk. The internal names deliberately do not: the Django app label
`roompulse` and every table it owns, the `/api/roompulse/` routes, the
`X-AdminPulse-Session` header, and `RoomPulseLoginView`. Renaming the app
label would rewrite live QA and Live tables for no visible gain, so it stays.

A user's access to the tool is granted by `accounts.AppKey.HELPDESK`
(`'helpdesk'`), which the frontend checks by that exact string. It used to be
`'roompulse'`; migration `accounts/0007` rewrote the existing grants.

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

## The Super Admin's overview

`views/overview.py` is one call answering three questions: what is waiting on
somebody (with how long the oldest has waited), what is happening right now,
and whether the setup itself is broken — nobody assigned to a queue, no rooms,
an empty or stale directory. All of it was visible somewhere already across
five tabs, which is fine for doing a job and useless for noticing one.

Anything actionable carries the number that makes it actionable: a queue's
depth means little, `oldest_days` and `stale` are what someone acts on.

## The employee master comes from the directory

`accounts.PortalUser` is the company directory, synced from HRMS. AdminPulse
pulls from it (`roompulse/directory.py`) instead of holding a second copy
uploaded from a spreadsheet — two copies disagreed the moment either changed.

`Employee.source` is `directory` or `manual`. A sync refreshes the first,
marks leavers inactive (never deletes — their name is on tickets), and **never
touches the second**: people are added by hand precisely because they are not
in HRMS, so a sync can never bring them back.

`resolve_role` treats somebody as an employee if they are **on the directory**,
falling back to the `@apisindia.com` domain. The domain alone was the rule, and
it shut out most of the company: only about a quarter of people have a company
address, the rest having the personal ones HRMS holds.

Who resolves Admin vs IT tickets stays `AdminUser`, set by the super admin.
A sync mirrors it onto `Employee.role` for display but never changes it.

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

## Help Desk tells people what happened (`roompulse/notify.py`)

Every step a ticket, item request or room booking takes sends **one short
plain-text email to the one side that did not take it**. Approving mails the
requester, not the approver; the requester withdrawing their own ticket mails
the desk, not themselves. Raising something sends two: work to the person it
was addressed to, an acknowledgement to the person who raised it.

Work logged after the fact (`origin='logged'`) sends nothing — nobody is
waiting on it, and it is the one thing that would arrive every day.

Two rules when adding a step:

- Call `notify.moved(kind, obj, action, actor_email, remarks)` **after** the
  save and the `_log()`, never before. A mail about a step that then fails to
  commit is worse than no mail.
- Add the wording to `OWN` (what the requester is told) — and to `DESK` only
  if the requester can be the one who takes that step. An action with no
  entry in either sends nothing, silently and by design.

`_safe` wraps every entry point and the send goes on a thread over SMTP, so a
mail server that is down or slow cannot fail or stall a ticket action. That is
the contract: **Help Desk does not depend on Gmail being up.**

`PORTAL_BASE_URL` in `.env` is the link at the foot of each mail, and it is
the intranet's **front door only**: the frontend has no router — which tool is
open is React state, not a path — so a deep link would land on nothing. Unset,
the line is left out rather than guessed: QA and Live are different hosts and a
mail pointing at the wrong one is worse than no link at all. Sender is the same
account the sign-in code comes from.

## Attendance photos are checked on the server (`user_management/photo_check.py`)

The field attendance tool flags punch-in photos that are black, blown out,
featureless, or appear to have nobody in them. **It is on the server because
it cannot be anywhere else:** reading a photo's pixels out of a canvas needs
the host serving it to send `Access-Control-Allow-Origin`, and BIZOM's does
not — the image displays in an `<img>` and is unreadable to every line of
code on the page.

It measures brightness, detail and skin-tone share. Brightness and detail are
solid. **The skin share is a hint and must stay labelled as one** — it is a
colour range, not a face detector, and a mask, gloves, coloured light or a
dark room all push it down on a photo of a present human being. It reports
`no_person` ("may not be a person"), never an accusation. Detail is measured
across the colour channels as well as luma, because two colours can share a
brightness almost exactly and a luma-only test calls a picture full of things
a flat grey card.

**`url_problem()` is load-bearing.** This endpoint fetches URLs out of a
spreadsheet column, which is an SSRF hole done naively — `http://127.0.0.1:8000/`
in that column would have the server fetch its own API and hand back the
answer. Every URL and every redirect hop is resolved and every address it
answers with must be public. Do not relax it to a host-name match: a name
somebody else controls can point at 127.0.0.1.

`/photo-thumbs/` is the same fetcher re-encoding each photo to a ~96px JPEG
data URI, for the shareable JPG sheets the frontend draws. Same reason it is
server-side: a BIZOM-hosted image drawn onto a canvas taints it and `toBlob`
then refuses, so the sheet is not produced at all. A data URI has no origin.
A photo it cannot fetch is simply absent from the answer — one grey square on
a sheet, not a failed download.

Verdicts are cached for a day by URL hash; `unreadable` is deliberately not
cached, because a host being down is not a fact about the photo.

## Analytics is windowed

`views/analytics.py` measures everything over `?days=` (default 30) and also
returns all-time `totals`. Anything added to the report needs both, or a quiet
period is indistinguishable from an empty system and the dashboard looks
broken. It covers bookings, item requests **and** tickets — the helpdesk was
missing from it at first, which is most of what people actually use.

## Confidential vacancies

`Vacancy.confidential` marks a position being filled quietly. Such rows are
**never** in the ordinary `GET /api/vacancies/` — for anyone, superadmin
included — because that list feeds the dashboard card and the referral form's
position dropdown. `Vacancy.published()` excludes them too. They are read
through `?scope=confidential`, which answers only superadmins and people with
`PortalUser.can_view_confidential_vacancies` (granted in the Admin Console;
`sees_confidential_vacancies` is the one check). Adding one is approved on
creation, and anyone with the grant may close or reopen one; everything else
about vacancies stays superadmin-only.

## Dashboard content goes through one gate

Anything a person can put on the intranet home page — a vacancy, a wall photo,
an announcement, a referral, a Daily News story — inherits
`accounts.moderation.ModeratedContent` and **starts PENDING**. Nothing reaches
the dashboard until a superadmin approves it, and every create, edit, approve,
reject and delete is written to `ActivityLog`.

Two rules when adding another kind:

- Filter with the model's `published()` classmethod, never by hand. A view that
  builds its own filter is how a pending row reaches the home page.
- Add one line to `CONTENT_TYPES` in `accounts/views_moderation.py` and the
  approval console picks it up — there is nothing else to wire.

A superadmin's own submission is auto-approved: they are the approving
authority, so queueing work for themselves adds a step without adding control.
The activity log still records that they created it.

Content that is a file but not an image (a PDF, a spreadsheet) overrides
`moderation_link()` instead of `moderation_preview()`; the console shows it as
"Open file". A reviewer approving a document they cannot open is not reviewing
it.

## Policies & Guidelines (`policies.PolicyDocument`)

One table for SOPs, manual policies, templates, work instructions and formats;
`category` holds the label the page shows, and must match `CATEGORY_LABELS` in
the frontend's `PoliciesPage.tsx`. It goes through the moderation gate above.

- The uploader can delete their own document (the "added by accident" case);
  a superadmin can delete any. `PolicyDocument.can_delete` is the one rule.
- Files are allowlisted by extension **and** checked against their opening
  bytes (`MAGIC_FOR_EXT`) — they are served from `MEDIA_URL` on our origin.
- `doc_approved_by` / `doc_reviewed_by` are who signed the document off in the
  business. They are not `reviewed_by`, the superadmin who let it onto the
  intranet.
- The 11 PDFs in the frontend's `public/Policies/` are part of the build and
  are not rows here. A superadmin can still take one off the page: that
  writes a `BuiltInRemoval` (filename, who, when) and the page hides it.
  Delete that row in Django admin to put the policy back.

## Daily News (`noticeboard.NewsItem`)

The strip under Your Tools. Stories about the market APIS sells into — APIs,
food APIs, nutraceuticals, honey — and about the company. Not the same thing as
the frontend's `WHATS_NEW`, which is news about the intranet's own tools and
ships with a build.

- `published_on` is the day the story is *about*, not the day it was typed —
  otherwise the strip claims a week-old piece broke this morning.
- A picture is either an upload (ours, keeps working) or `image_url`
  (someone else's, can rot). `_news()` resolves the two to one field and the
  card falls back to a plain tile when the link dies.
- `GET` returns the newest 12; `?scope=all` returns everything, which is what
  "View all" and the console ask for.

Feeds (`NewsSource`, `newsfeed.py`) fill it on a schedule, run by
`manage.py fetch_news` from cron -- a management command, not Celery, for the
reason spelled out in `accounts/management/commands/sync_hrms.py`.

- **The two seeded feeds publish straight to the dashboard** (`auto_publish`),
  decided after the queue was built and used: one that fills every morning gets
  bulk-approved unread within a fortnight, so it is a gate in name only and a
  chore as well. A source added later still starts off needing approval -- the
  switch is per source, not global.
- What makes that safe is the lifecycle, not vigilance. `NewsItem.for_strip()`
  shows only what is approved, unexpired and newer than `STRIP_MAX_AGE_DAYS`
  (pinned bypasses it), so the strip empties itself. `purge_old()` runs at the
  end of every fetch and deletes fetched rows past `PURGE_AFTER_DAYS`;
  hand-written and pinned rows are never purged.
- Nothing unapproved ever reaches the strip, for anyone, superadmin included.
  The console reads `?scope=all`; the dashboard reads the strip.
- Google ranks by relevance, not date, so `resolved_url()` appends `when:Nd`
  from `max_age_days`. Without it a narrow query answers with its best matches
  going back years -- one real query returned results from 2013.
- **Never truncate a feed link.** Google News article links are opaque encoded
  ids: in one real feed 13 of 39 were over 500 characters and the longest was
  824. A clipped link is not a shorter link, it is a broken one -- Google
  answers it with "400, malformed". `source_url`/`image_url` are 2000.
- `external_id` is a SHA-256 of the feed's id, not the id itself. It is
  indexed, and MySQL's utf8mb4 limit is 3072 bytes, so a column wide enough for
  the raw id could carry no index at all.
- Junk titles are judged on the *final* headline. Google appends
  " - The Publisher" to every title, so a placeholder arrives as
  "BlogDescription - PIB" and passes a check made before the suffix is stripped.
- A source stops fetching at `BACKLOG_LIMIT` unreviewed stories and records
  that as `paused`, not `failed` -- nothing is broken.

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

## Policies & Guidelines — everything is a PDF

Uploads to `policies/` are stored as PDF only. Word / Excel / PowerPoint are
converted on upload by LibreOffice headless (`policies/pdf_convert.py`), so
**LibreOffice must be installed on QA and Live**
(`sudo apt install libreoffice-core libreoffice-writer libreoffice-calc libreoffice-impress`).
Without it, non-PDF uploads are refused with "save it as a PDF" — never
stored as-is. `original_name` keeps the name of the file that was sent.
Documents filed before this change: `manage.py convert_policies_to_pdf`
(`--dry-run` first).

## Medical Insurance (`insurance/`) — we are the partner, not the portal

The insurer's "SSO Auto Login — Partner Integration Guide v1.0" describes
**one** thing: a redirect that signs an employee in to the insurer's own
Employee Portal. We encrypt the person's details with a shared secret and
send their browser to `/sso/login/<token>`.

**There is no data API in that document.** No endpoint returns the e-card,
the sum insured, the members covered or a claim's status. So the intranet
cannot render any of it — this tool takes a person to the insurer's page and
holds no medical information of its own. If those details are ever to appear
inside the intranet, that is a second, separate ask of the insurer.

- `sso.make_token()` is AES-256-GCM, key = `SHA256(secret)`, fresh 12-byte IV
  every call, full 16-byte tag, `b64url(iv).b64url(ct).b64url(tag)` with the
  padding stripped. `AESGCM.encrypt` appends the tag to the ciphertext, which
  is NOT the shape the insurer wants — it is split back off.
- **The secret never leaves this process.** Anyone holding it can mint a
  token for any employee's address and read that person's medical records, so
  there is no endpoint here that takes an employee id: a token is minted for
  the caller's own session and nothing else.
- Unset `INSURANCE_SSO_SECRET` / `INSURANCE_SSO_BASE_URL` means the tool
  reports itself as not connected. It never mints a token nobody can decrypt.
- Section 3 allows the employee code when a person has no email, which is the
  ~50 directory entries HRMS has no address for, so both are always sent.
- `INSURANCE_SSO_EMPLOYEE_ID_FIELD` exists because the guide writes that one
  field as prose ("Employee ID") while writing the rest as identifiers — its
  real spelling is not actually pinned down and needs confirming with the
  insurer. Settable so that is an `.env` change, not a release.
- A launch is written to `ActivityLog`; **the URL is not**, because it carries
  the token and section 10 says not to log it.
- `cryptography` is now named in `requirements.txt`. It was already installed
  on both servers as somebody else's transitive dependency — an accident that
  would take this tool down with an ImportError the day pip resolved
  differently.

## Attendance (`attendance/`) — live from Pocket HRMS

`GetAttendanceAPI`, read on demand for a date or a range. A separate tool from
the Data Extractor, with its own grant (`AppKey.ATTENDANCE`), because it
carries every employee's working hours for any day with nobody exporting
anything first.

Everything below was confirmed against the live tenant on 19-11-2026 by
fetching 01/11–10/11 (718 employees, 7,180 rows). The vendor's document is
right about the shape and wrong or silent about most of this:

- **Dates go in as `yyyy-MM-dd` and come back as `dd/MM/yyyy`.** Nothing warns
  you; `_iso()` handles the asymmetry.
- **The range limit is 31 days inclusive, not the documented 30.**
  `01/10 → 01/11` is refused; `02/10 → 01/11` is fine. Longer spans are split
  by `windows()` and stitched.
- **`InTime` and `OutTime` can be the literal word `Unswiped`** — 649 out of
  7,180 in ten days. It is a punch that was never made, not a time, and
  `TotalWorkingHrs` is `-` on those rows. **No duration is derived from one**;
  `workedMins` stays `None` rather than being guessed, for the same reason
  BIZOM's 23:59 auto-close is not counted as a fifteen-hour day.
- **`-` is the empty value for every field.**
- `EarlyTime` / `LateTime` are **durations against that employee's own shift**,
  not clock times. `LateTime: 00:25` is twenty-five minutes late.
- `TotalWorkingHrs` is the raw punch-to-punch span (seen up to 16:07);
  `NormalWorkingHours` is that capped at the shift (never above 08:00). They
  answer different questions and both are kept.
- **`OTHours` was `-` on all 7,180 rows** — this tenant does not compute
  overtime. `overtimeTracked: false` is returned so no screen draws a column
  of dashes.
- **A row exists for every employee on every date, future dates included** —
  a future range answers 200 with a full set of blanks. So `noRecord` means
  "nothing recorded", **not** absent. The feed has no leave, week-off or
  holiday column, so a day off, an approved leave and a missed punch are
  indistinguishable. `holidays()` (GetHolidays) recovers the one piece of that
  the API will give us; a failure to fetch it does not fail the request.
- The feed has **no location, department or manager**, so "Delhi HO" is
  impossible to ask of this API. Those are joined on from `accounts.PortalUser`
  by employee code in `views.py`, and a code not on the directory is marked
  `offDirectory` rather than dropped (candidate codes appear in the feed).

**Two different "late" numbers, on purpose.** `late` is HRMS against the
person's shift; `afterCutoff` is an in-punch later than
`ATTENDANCE_LATE_CUTOFF` (11:00, strict `>`) on the clock. On one real
two-day sample they were 502 and 49. Do not collapse them — they answer
different questions and the screen says so next to the figures.

A fetch is cached ten minutes by range (`?refresh=1` bypasses). 31 days of
the whole company is ~22,000 rows and ~6 MB.
