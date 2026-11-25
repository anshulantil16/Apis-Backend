"""Sending a colleague a wish on their birthday or work anniversary.

The home dashboard has always shown whose birthday is coming up. This is what
turns that from a notice board into something you can act on: a button on each
row, and the wish arriving on the person's own dashboard on the day.

Two rules carry the whole design.

**A wish is addressed to a date.** Everything below computes the day the wish
is FOR from the recipient's own record, and ``Wish.occasion_date`` stores it.
The recipient's feed asks for rows dated today, so a wish sent early waits and
a wish from yesterday is already gone. No expiry task exists, because the one
that cannot fail is the one that does not have to run.

**You may only wish somebody who is actually celebrating.** Not a general
message channel wearing a party hat: the recipient must have a birthday or an
anniversary within ``WINDOW_DAYS``, and the server checks it on every send.
Messages between colleagues for any other reason belong in Messages, which is
built for it and keeps a thread.

``next_occurrence`` is shared with ``accounts.views.CelebrationsView`` on
purpose. If the card said 5 January and this module computed 6 January, every
wish sent from that card would land on a day the recipient is not celebrating,
and the bug would be invisible until somebody's birthday.
"""
from datetime import date, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from config.tz import IST

from .models import PortalUser, Wish

# How far ahead you may wish somebody. The card shows a week; "View all" runs
# to the end of the month, which is at most 30 days out, so 31 covers every
# row either of them can show. A row you can see is a row you can wish.
WINDOW_DAYS = 31

# A ceiling on one person's sends in a day. The real limit is the number of
# people celebrating -- a dozen on a normal day -- so this never touches
# somebody wishing every colleague on the list. It is here so a script with a
# stolen session token cannot write a million rows.
MAX_PER_DAY = 300

# What the recipient reads when the sender added nothing of their own.
DEFAULT_MESSAGE = {
    Wish.Occasion.BIRTHDAY: 'Happy birthday!',
    Wish.Occasion.ANNIVERSARY: 'Happy work anniversary!',
}


def today_ist():
    return timezone.localtime(timezone.now(), IST).date()


def next_occurrence(d, today):
    """The next time the day-and-month of ``d`` comes round, today included.

    Returns None if ``d`` is missing. Compared on month and day only, so it
    wraps across the new year: on 20 December, a 5 January birthday is 16 days
    away rather than 349 behind.

    29 February falls back to the 28th in a non-leap year, so somebody born on
    a leap day still has a birthday every year.
    """
    if not d:
        return None
    day = min(d.day, 28) if (d.month == 2 and d.day == 29) else d.day
    try:
        nxt = date(today.year, d.month, day)
    except ValueError:
        return None
    return nxt if nxt >= today else date(today.year + 1, d.month, day)


def occasion_field(occasion):
    return ('date_of_birth' if occasion == Wish.Occasion.BIRTHDAY
            else 'date_of_joining')


def occasion_date_for(person, occasion, today):
    """The day this person's next birthday/anniversary falls on, or None.

    None means there is nothing to wish them for: the date is missing from the
    directory, it is further off than ``WINDOW_DAYS``, or -- for an
    anniversary -- they have not completed a year yet, which is the same rule
    the celebrations card applies ("1 year" is the point of an anniversary, and
    a first one has not happened).
    """
    original = getattr(person, occasion_field(occasion), None)
    nxt = next_occurrence(original, today)
    if not nxt:
        return None
    if (nxt - today).days > WINDOW_DAYS:
        return None
    if occasion == Wish.Occasion.ANNIVERSARY and nxt.year - original.year < 1:
        return None
    return nxt


def send(sender, recipient_id, occasion, message, today=None):
    """Record a wish. -> (payload, None) or (None, (error, status)).

    Re-sending to the same person for the same occasion edits the message
    already there rather than adding a row, so the button is safe to press
    twice and a feed cannot be flooded.
    """
    today = today or today_ist()

    if occasion not in Wish.Occasion.values:
        return None, ('That is not an occasion we send wishes for.', 400)

    recipient = PortalUser.objects.filter(id=recipient_id, is_active=True).first()
    if not recipient:
        return None, ('We could not find that person in the directory.', 404)
    if recipient.id == sender.id:
        # Harmless, but it would put your own name in your own feed on your own
        # birthday, which reads as a bug rather than as a joke.
        return None, ('You cannot send yourself a wish.', 400)

    occasion_date = occasion_date_for(recipient, occasion, today)
    if not occasion_date:
        label = 'birthday' if occasion == Wish.Occasion.BIRTHDAY else 'work anniversary'
        return None, (f'{recipient.name} has no {label} coming up to wish them for.', 400)

    message = ' '.join((message or '').split())[:280]

    # A rolling 24 hours, not "rows dated today": created_at is stored in UTC
    # and `today` is an IST calendar date, so comparing the two would move the
    # ceiling by five and a half hours and leave it wrong every evening.
    since = timezone.now() - timedelta(hours=24)
    if Wish.objects.filter(sender=sender, created_at__gte=since).count() >= MAX_PER_DAY:
        return None, ('That is a lot of wishes for one day. Try again tomorrow.', 429)

    try:
        with transaction.atomic():
            wish, created = Wish.objects.update_or_create(
                sender=sender, recipient=recipient,
                occasion=occasion, occasion_date=occasion_date,
                defaults={'message': message, 'sender_name': sender.name})
    except IntegrityError:
        # Two taps landing at once. The wish is already there, which is the
        # outcome the caller wanted.
        return None, ('That wish is already on its way.', 409)

    return {
        'sent': True,
        'created': created,
        'to': recipient.name,
        'occasion': occasion,
        'wish_id': wish.id,
        # So the sender is told the truth about when it arrives rather than
        # being left to assume it has already landed.
        'arrives_on': occasion_date.strftime('%d %b').lstrip('0'),
        'arrives_today': occasion_date == today,
    }, None


def feed_for(user, today=None):
    """The wishes to show this person right now: the ones dated today.

    Yesterday's are not deleted, they simply stop matching. See the module
    docstring.
    """
    today = today or today_ist()
    rows = (Wish.objects.filter(recipient=user, occasion_date=today)
            .select_related('sender').order_by('created_at'))
    return [{
        'id': w.id,
        'from': w.sender_name or (w.sender.name if w.sender else 'A colleague'),
        'department': w.sender.department if w.sender else '',
        'occasion': w.occasion,
        'message': w.message or DEFAULT_MESSAGE[w.occasion],
        # True when the sender typed something, so the card can tell a written
        # note apart from a one-tap wish without comparing strings.
        'has_message': bool(w.message),
        'seen': w.seen_at is not None,
    } for w in rows]


def my_occasions(user, today=None):
    """Which of today's celebrations are this person's own.

    The banner needs to know whether to say "Happy Birthday" or "5 years
    today", and a person can have both on one day.
    """
    today = today or today_ist()
    out = []
    for occasion in Wish.Occasion.values:
        original = getattr(user, occasion_field(occasion), None)
        if next_occurrence(original, today) != today:
            continue
        if occasion == Wish.Occasion.ANNIVERSARY:
            years = today.year - original.year
            if years < 1:
                continue
            out.append({'occasion': occasion, 'years': years})
        else:
            out.append({'occasion': occasion, 'years': None})
    return out


def wished_by(user, today=None):
    """``{(recipient_id, occasion)}`` this person has already wished.

    Lets each row on the cards show "Wished" instead of offering the button a
    second time -- the alternative being that you tap, nothing visibly changes,
    and you tap again.
    """
    today = today or today_ist()
    return {
        (r_id, occ) for r_id, occ in
        Wish.objects.filter(sender=user, occasion_date__gte=today)
                    .values_list('recipient_id', 'occasion')
    }
