"""Filling the Goal Setting employee master from the company directory.

Goal Setting was built to load its people from a spreadsheet, the way
Appraisal Hub does, so it could be run for one department before the rest of
the company was on any other tool. That is no longer the situation: the
intranet has a directory of its own (``accounts.PortalUser``, synced nightly
from Pocket HRMS) and Help Desk already reads from it rather than keeping a
second copy. Two copies of the same people disagree the moment either
changes, and the spreadsheet was the one going stale.

So an admin can now press a button instead of downloading a template.

What the directory can and cannot give us
-----------------------------------------
Measured against the live HRMS tenant on 25-11-2026, 697 current employees:

  * **The reporting manager is clean.** It arrives as ``(CODE)NAME``, so the
    code is explicit: 634 of 697 carry one and every one of those 634 points
    at somebody who currently works here. Six people are listed as their own
    manager -- the top of the tree, plus some data entry -- and those are
    blanked, because a goal sheet that routes to its own owner can never be
    signed off.
  * **The HOD is a bare NAME, with no code at all.** It has to be matched
    back to a person by name, and that does not always work: of 20 distinct
    HOD names, 9 match exactly one employee, 2 are ambiguous (two different
    "Sunil Kumar"s), and 9 match nobody because of how they are spelt in the
    HOD field -- "PANKAJ TRIPATHII", "Rahul Dutta Sharma" for Rahul Dutt
    Sharma, "Irshad Alam" and "Ershad Alam" for one man. That still covers
    562 of the 637 people who have an HOD named at all.

    **The unmatched ones are reported, never guessed.** A fuzzy match that is
    wrong does not look wrong -- it silently routes somebody's goals to the
    wrong head of department, who then approves them. The sync hands back the
    names it could not place so HR can correct them in HRMS, which fixes it
    for every tool at once rather than only this one.

Nothing here writes to the directory. It is read-only to Goal Setting.
"""
import re

from django.db import transaction
from django.utils import timezone

from accounts.models import PortalUser

from .models import EmployeeProfile

# What HR types into the HOD field meaning "nobody".
NOT_A_PERSON = {'', '-', 'na', 'n/a', 'none', 'nil', 'null'}


def _norm(name):
    """A name reduced to letters, for matching across spacing and case.

    Deliberately NOT a fuzzy match -- see the module docstring. This collapses
    "Rahul  Dutt  Sharma" and "rahul dutt sharma", and nothing else.
    """
    return re.sub(r'[^a-z]', '', (name or '').lower())


def _name_index(people):
    """``{normalised name: code}``, for names belonging to exactly one person.

    A name two people share is left out entirely rather than resolved to
    whichever row happened to come first.
    """
    seen = {}
    for p in people:
        key = _norm(p.name)
        if not key:
            continue
        seen.setdefault(key, set()).add((p.employee_code or '').strip())
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}


def _user_type(code, reports_to, heads):
    """What this person is to OTHER people, worked out from the directory.

    Not a label somebody typed into a column: an HOD is somebody named as an
    HOD, and a manager is somebody who has reports. Reading a role off a
    typed column is what stopped every manager in the company from filling in
    their own goal sheet.
    """
    if code in heads:
        return 'hod'
    if code in reports_to:
        return 'manager'
    return 'employee'


def sync_from_directory(only_active=True):
    """Refresh every directory-sourced profile from ``accounts.PortalUser``.

    **Only people who currently work here are imported**, the same rule TADA
    has always applied (`tada.sync.sync_from_master`). The directory keeps a
    row for somebody who has left, marked inactive; importing those gave the
    People tab a tail of ex-employees who can never fill in a goal sheet.

    It also means a manager or an HOD who has left no longer resolves, so
    their reports end up with that field empty and visible to the admin --
    which is the honest answer. A goal sheet routed to somebody who does not
    work here waits forever and only the admin can free it.

    Somebody who leaves is still deactivated rather than deleted: they stop
    arriving in the sync, and the pass at the end marks the row inactive.

    -> a dict the admin screen reports verbatim, including the HOD names that
       could not be placed.
    """
    rows = PortalUser.objects.all()
    if only_active:
        rows = rows.filter(is_active=True)
    people = list(rows.only(
        'employee_code', 'name', 'email', 'designation', 'department',
        'reporting_manager_code', 'hod_code', 'hod_name', 'is_active'))
    people = [p for p in people if (p.employee_code or '').strip()]

    by_code = {(p.employee_code or '').strip().upper(): p for p in people}
    by_name = _name_index(people)

    # Resolve each person's HOD to a code once, and remember what failed.
    hod_for = {}
    unresolved = {}
    for p in people:
        code = (p.employee_code or '').strip().upper()
        direct = (p.hod_code or '').strip()
        if direct and direct.upper() in by_code:
            hod_for[code] = direct
            continue
        raw = (p.hod_name or '').strip()
        if raw.lower() in NOT_A_PERSON:
            continue
        match = by_name.get(_norm(raw))
        if match:
            hod_for[code] = match
        else:
            unresolved[raw] = unresolved.get(raw, 0) + 1

    # Who is a manager, and who is an HOD, to somebody else.
    reports_to = {(p.reporting_manager_code or '').strip().upper()
                  for p in people if (p.reporting_manager_code or '').strip()}
    heads = {c.upper() for c in hod_for.values()}

    seen = set()
    created = updated = 0

    with transaction.atomic():
        for p in people:
            code = (p.employee_code or '').strip()
            upper = code.upper()
            seen.add(upper)

            manager = (p.reporting_manager_code or '').strip()
            # Somebody listed as their own manager cannot review their own
            # sheet -- six people arrive that way. No reviewer, which the
            # admin can see and fix, beats a loop nobody can act on.
            if manager.upper() == upper:
                manager = ''
            if manager and manager.upper() not in by_code:
                manager = ''

            hod = hod_for.get(upper, '')
            if hod.upper() == upper:
                hod = ''

            fields = {
                'name': (p.name or code)[:200],
                'email': (p.email or '')[:254],
                'designation': (p.designation or '')[:150],
                'department': (p.department or '')[:150],
                'reporting_manager_id': manager[:50],
                'hod_id': hod[:50],
                'is_active': bool(p.is_active),
                'source': 'directory',
                'synced_at': timezone.now(),
            }

            row = EmployeeProfile.objects.filter(employee_id__iexact=code).first()
            if row is None:
                EmployeeProfile.objects.create(
                    employee_id=code,
                    user_type=_user_type(upper, reports_to, heads),
                    **fields)
                created += 1
                continue

            # An admin stays an admin. That role is a grant somebody made
            # inside this tool; the directory knows nothing about it and must
            # not revoke it.
            if row.user_type != 'admin':
                row.user_type = _user_type(upper, reports_to, heads)
            for k, v in fields.items():
                setattr(row, k, v)
            row.save()
            updated += 1

        # People who have left the directory. Marked inactive, never deleted:
        # their name is on goal sheets, versions and the activity trail, and a
        # master that forgets them makes all of that unreadable.
        gone = [r for r in EmployeeProfile.objects.filter(
                    source='directory', is_active=True)
                if r.employee_id.strip().upper() not in seen]
        for r in gone:
            r.is_active = False
            r.save(update_fields=['is_active'])

    with_manager = sum(
        1 for p in people
        if (p.reporting_manager_code or '').strip()
        and (p.reporting_manager_code or '').strip().upper()
        != (p.employee_code or '').strip().upper())

    return {
        'created': created,
        'updated': updated,
        'deactivated': len(gone),
        'manual_untouched': EmployeeProfile.objects.filter(source='manual').count(),
        'with_manager': with_manager,
        'with_hod': len(hod_for),
        'unresolved_hods': sorted(
            [{'name': k, 'people': v} for k, v in unresolved.items()],
            key=lambda d: -d['people']),
        'total': len(people),
    }
