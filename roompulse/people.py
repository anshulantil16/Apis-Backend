"""One place that answers "what is this person called?".

Names were being taken from whatever the browser sent when a request was
raised, or from the login handle on the session. That is how the screens
ended up reading "Anshulantil01", "2022Bth005" and "Rainy222004" -- those are
sign-in names, not people's names, and a report that a manager reads has to
say Kanchan.

The company's own record of who people are is accounts.PortalUser, synced
from HRMS (see accounts/management/commands/sync_hrms.py). That is the source
of truth here, ahead of the directory copy in roompulse.Employee, ahead of
whatever the super admin typed into the roster, and far ahead of the string
that came in on a request.

Resolved at display time rather than stored, so a name corrected in HRMS is
corrected everywhere, including on work recorded last month.
"""
from accounts.models import PortalUser

from .models import AdminUser, Employee


def _clean(value):
    value = (value or '').strip()
    return value if value else ''


def name_map(emails):
    """-> {lowercased email: proper name} for the ones we can answer.

    Three queries whatever the size of the list, so a page of five hundred
    rows costs the same as one.
    """
    wanted = {(e or '').strip().lower() for e in emails if e}
    wanted.discard('')
    if not wanted:
        return {}

    found = {}
    # Best source first, and stop as soon as everybody is accounted for.
    # Most lists are entirely people in HRMS, so this is normally one query
    # whatever the size of the list -- and this runs on every queue page.
    for model in (PortalUser, Employee, AdminUser):
        missing = wanted - set(found)
        if not missing:
            break
        for email, name in model.objects.filter(
                email__in=missing).values_list('email', 'name'):
            name = _clean(name)
            if name:
                found[email.lower()] = name
    return found


def name_for(email, fallback=''):
    """One person, for the odd place that has only one to look up."""
    return name_map([email]).get((email or '').strip().lower()) \
        or _clean(fallback) or (email or '').split('@')[0]


def apply_names(rows, *pairs):
    """Fill proper names into already-serialized rows, in one pass.

    `pairs` are (email_key, name_key) tuples. The stored name is kept when
    the person is in none of the tables -- somebody outside the company, or
    a row from before the directory had them.
    """
    rows = list(rows)
    emails = [r.get(ek) for r in rows for ek, _ in pairs]
    known = name_map(emails)
    for row in rows:
        for email_key, name_key in pairs:
            proper = known.get((row.get(email_key) or '').strip().lower())
            if proper:
                row[name_key] = proper
    return rows
