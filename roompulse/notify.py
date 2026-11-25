"""Telling people what happened to their request.

Everything in Help Desk is somebody waiting on somebody else, and until now
the only way to find out that your ticket had been approved, picked up or
resolved was to open the tool and look. So nobody looked, and the desk got
asked over WhatsApp instead -- which is the thing the tool was built to stop.

**One mail per step, to the one person the step is news for.** Who that is
depends on which way the request just moved:

    raised       -> the person it was addressed to (here is work), and the
                    person who raised it (we have it, it is with so-and-so)
    approved     -> whoever raised it
    rejected     -> whoever raised it, with the reason
    started      -> whoever raised it
    resolved     -> whoever raised it
    fulfilled    -> whoever raised it
    cancelled    -> the OTHER side: the desk if the requester withdrew it,
                    the requester if the desk cancelled it
    transferred  -> the person it has been passed to, and the person who
                    raised it, so they know whose desk it is on now
    released     -> whoever booked the room, unless they released it
    logged work  -> nobody. A job written up after the fact has nobody
                    waiting on it and nothing to tell them.

Nobody is ever mailed about something they did themselves.

**A mail that fails must never fail the request.** Approving a ticket and
telling somebody about it are two different jobs, and SMTP is the one of them
that is allowed to be unavailable. Every entry point here is wrapped by
`_safe`, so the worst a broken mail server can do is leave a line in the log
while the ticket moves exactly as it should.

Over SMTP the send goes on a background thread for the same reason: Gmail
takes a second or two to answer, and an admin clearing twenty tickets should
not wait forty seconds for it. The console and in-memory backends send inline
-- they cost nothing, and a test that has to wait on a thread is a test that
fails one run in fifty.
"""
import logging
import threading

from django.conf import settings
from django.core.mail import EmailMessage

from .people import name_map
from .timefmt import span

log = logging.getLogger(__name__)

SIGN_OFF = '-- APIS Help Desk'


# -- plumbing -------------------------------------------------------------

def _sender():
    """The same address the sign-in code comes from, resolved the same way
    (see roompulse/views/auth.py). One account, so Help Desk mail arrives as
    one sender in everybody's inbox rather than two."""
    return (getattr(settings, 'OFFER_LETTER_EMAIL_HOST_USER', None)
            or settings.EMAIL_HOST_USER or settings.DEFAULT_FROM_EMAIL)


def _link_line():
    """The intranet's front door, and deliberately not a deep link: the
    frontend has no router -- which tool is open is React state, not a path
    -- so anything after the host would land on a page that does not exist."""
    base = (getattr(settings, 'PORTAL_BASE_URL', '') or '').rstrip('/')
    return f'Open the intranet and go to Help Desk: {base}' if base else ''


def _in_background():
    return 'smtp' in (getattr(settings, 'EMAIL_BACKEND', '') or '')


def _deliver(to, subject, body):
    try:
        EmailMessage(subject=subject, body=body,
                     from_email=_sender(), to=[to]).send(fail_silently=False)
    except Exception:
        # Logged, not raised: see the module docstring. The caller has
        # already committed the thing this mail is about.
        log.exception('Help Desk: could not notify %s -- %s', to, subject)


def _post(to, subject, lines):
    """One address, one mail. Several recipients get their own copy rather
    than appearing in each other's To line -- a ticket names the person who
    raised it and what broke, and the desk roster is not public."""
    to = (to or '').strip().lower()
    if not to or '@' not in to:
        return
    parts = [l for l in lines if l is not None]
    link = _link_line()
    if link:
        parts += ['', link]
    parts += ['', SIGN_OFF]
    body = '\n'.join(parts).strip() + '\n'
    subject = subject[:120]
    if _in_background():
        threading.Thread(target=_deliver, args=(to, subject, body),
                         daemon=True).start()
    else:
        _deliver(to, subject, body)


def _safe(fn):
    """Nothing in here is worth a 500 on a ticket that has already moved."""
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception:
            log.exception('Help Desk: notification %s failed', fn.__name__)
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def _fields(pairs):
    """A short aligned block. Blank values are dropped rather than printed as
    an empty row -- half the fields on a request are optional."""
    rows = [(k, str(v).strip()) for k, v in pairs if str(v or '').strip()]
    if not rows:
        return []
    width = max(len(k) for k, _ in rows)
    return [f'{k.ljust(width)} : {v}' for k, v in rows]


def _who(email, fallback=''):
    """The person's real name, from the directory. See roompulse/people.py:
    what the browser sent is a sign-in handle often enough that it is not
    worth printing in a mail somebody reads."""
    email = (email or '').strip()
    if not email:
        return fallback or ''
    found = name_map([email]).get(email.lower())
    return found or fallback or email


def _detail(text, limit=600):
    """The description, kept short. The whole of it is on the ticket; this is
    enough to know what the mail is about without opening anything."""
    text = (text or '').strip()
    if not text:
        return []
    if len(text) > limit:
        text = text[:limit].rstrip() + ' ...'
    return ['', 'Details:', text]


def _remarks(remarks, label='Remarks'):
    remarks = (remarks or '').strip()
    return ['', f'{label}: {remarks}'] if remarks else []


# -- what each kind of request looks like in a mail -----------------------

def _ticket_facts(t):
    return [
        ('Ticket',    f'#{t.id}'),
        ('Subject',   t.subject),
        ('Priority',  t.get_priority_display()),
        ('Category',  t.get_category_display()),
        ('Raised by', _who(t.requested_by_email, t.requested_by_name)),
        ('Assigned',  t.assigned_to_name or t.assigned_to_email),
    ]


def _resource_facts(r):
    return [
        ('Request',   f'#{r.id}'),
        ('Item',      f'{r.item_name} x {r.quantity}'),
        ('Category',  r.get_category_display()),
        ('Urgency',   r.get_urgency_display()),
        ('Needed by', r.needed_by.strftime('%d %b %Y') if r.needed_by else ''),
        ('Raised by', _who(r.requested_by_email, r.requested_by_name)),
        ('Assigned',  r.assigned_to_name or r.assigned_to_email),
    ]


def _booking_facts(b):
    return [
        ('Booking',   f'#{b.id}'),
        ('Room',      str(b.room)),
        ('When',      f'{b.date:%a %d %b %Y}, '
                      f'{span(b.start_time, b.end_time)}'),
        ('Purpose',   b.get_purpose_display()),
        ('Attendees', b.attendees),
        ('Raised by', _who(b.requested_by_email, b.requested_by_name)),
        ('Assigned',  b.assigned_to_name or b.assigned_to_email),
    ]


# Each returns the field PAIRS rather than finished lines, so a caller
# adding a row of its own -- the time a job took, the minute a room was
# freed -- gets it aligned with the rest instead of a column of its own.
SHAPE = {
    'ticket':   ('Ticket',  'ticket',  _ticket_facts),
    'resource': ('Request', 'request', _resource_facts),
    'booking':  ('Booking', 'booking', _booking_facts),
}


def _headline(kind, obj):
    if kind == 'ticket':
        return obj.subject
    if kind == 'resource':
        return obj.item_name
    return str(obj.room)


# -- raised ---------------------------------------------------------------

@_safe
def raised(kind, obj, detail=''):
    """Two mails: here is work, and we have your request.

    Both go out the moment it is raised rather than when it is triaged,
    because the gap between those two is exactly the stretch the person who
    raised it currently spends not knowing whether it arrived at all.
    """
    noun, lower, facts = SHAPE[kind]
    head = _headline(kind, obj)
    block = _fields(facts(obj))
    detail_lines = _detail(detail)

    assignee = (obj.assigned_to_email or '').strip().lower()
    requester = (obj.requested_by_email or '').strip().lower()

    if assignee and assignee != requester:
        _post(assignee,
              f'Help Desk -- New {lower} #{obj.id}: {head}',
              [f'A new {lower} has been raised and assigned to you.', '']
              + block + detail_lines
              + ['', 'Please review it in Help Desk.'])

    with_whom = obj.assigned_to_name or obj.assigned_to_email
    if obj.status == 'approved':
        # Raised by the desk itself, so there is nothing to wait for.
        state = 'It is approved already and needs no review.'
    elif with_whom:
        state = f'It is with {with_whom}. You will get an email at each step.'
    else:
        state = 'You will get an email at each step.'
    _post(requester,
          f'Help Desk -- Your {lower} #{obj.id} has been raised: {head}',
          [f'Your {lower} has been raised.', '']
          + block + detail_lines + ['', state])


# -- every step after that ------------------------------------------------

# What the person who RAISED it is told.
OWN = {
    ('ticket', 'approved'):    'Your ticket has been approved. IT Support will start on it shortly.',
    ('ticket', 'rejected'):    'Your ticket has not been approved.',
    ('ticket', 'started'):     'IT Support has started work on your ticket.',
    ('ticket', 'closed'):      'Your ticket has been resolved and closed.',
    ('ticket', 'cancelled'):   'Your ticket has been cancelled.',

    ('resource', 'approved'):  'Your request has been approved.',
    ('resource', 'rejected'):  'Your request has not been approved.',
    ('resource', 'fulfilled'): 'Your request has been fulfilled.',
    ('resource', 'cancelled'): 'Your request has been cancelled.',

    ('booking', 'approved'):   'Your room booking is confirmed.',
    ('booking', 'rejected'):   'Your room booking has not been approved.',
    ('booking', 'cancelled'):  'Your room booking has been cancelled.',
    ('booking', 'released'):   'Your meeting has been marked finished and the room released.',
}

# What the DESK is told, when the person who raised it is the one who acted.
DESK = {
    ('ticket', 'cancelled'):   'This ticket has been withdrawn by the person who raised it. Nothing further is needed.',
    ('resource', 'cancelled'): 'This request has been withdrawn by the person who raised it. Nothing further is needed.',
    ('booking', 'cancelled'):  'This booking has been cancelled by the person who made it. The room is free again.',
}

HEADLINE = {
    'approved': 'approved', 'rejected': 'not approved', 'started': 'in progress',
    'closed': 'resolved', 'fulfilled': 'fulfilled', 'cancelled': 'cancelled',
    'released': 'room released',
}


@_safe
def moved(kind, obj, action, actor_email='', remarks='', extra=None):
    """One step, to the one side that did not take it."""
    noun, lower, facts = SHAPE[kind]
    actor = (actor_email or '').strip().lower()
    requester = (obj.requested_by_email or '').strip().lower()

    if actor and actor == requester:
        to, text = obj.assigned_to_email, DESK.get((kind, action))
    else:
        to, text = obj.requested_by_email, OWN.get((kind, action))
    if not text:
        return

    label = 'Reason' if action in ('rejected', 'cancelled') else 'Remarks'
    by = _who(actor_email) if actor else ''
    _post(to,
          f'Help Desk -- {noun} #{obj.id} {HEADLINE.get(action, action)}: '
          f'{_headline(kind, obj)}',
          [text, ''] + _fields(list(facts(obj)) + list(extra or []))
          + _remarks(remarks, label)
          + (['', f'Actioned by: {by}'] if by else []))


@_safe
def transferred(kind, obj, to_email, to_name='', by_email='', reason=''):
    """Passed to somebody else. The desk changes; the stage does not."""
    noun, lower, facts = SHAPE[kind]
    head = _headline(kind, obj)
    by = _who(by_email) if by_email else ''

    _post(to_email,
          f'Help Desk -- {noun} #{obj.id} passed to you: {head}',
          [f'This {lower} has been passed to you' + (f' by {by}.' if by else '.'), '']
          + _fields(facts(obj)) + _remarks(reason, 'Reason')
          + ['', 'Please take it forward in Help Desk.'])

    holder = to_name or to_email
    requester = (obj.requested_by_email or '').strip().lower()
    if requester and requester != (to_email or '').strip().lower():
        _post(requester,
              f'Help Desk -- Your {lower} #{obj.id} is now with {holder}',
              [f'Your {lower} has been passed to {holder}, who will take it '
               f'forward. Nothing is needed from you.', '']
              + _fields(facts(obj)) + _remarks(reason, 'Reason'))


def mins_label(n):
    """45 -> '45 min', 90 -> '1 h 30 min'. Printed on a resolution mail
    because "how long did that take" is the first thing asked about it."""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return ''
    if n <= 0:
        return ''
    h, m = divmod(n, 60)
    if not h:
        return f'{m} min'
    return f'{h} h' if not m else f'{h} h {m} min'
