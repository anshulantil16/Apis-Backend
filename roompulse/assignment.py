"""Who a request is addressed to.

Each desk is two or three people. Until now everything they handled landed in
one pile all of them saw, so nothing could say which of them had dealt with
what, and the monthly report could only count whoever happened to press the
button.

So every request names one person, chosen when it is raised from the roster
the super admin keeps, and only that person works it afterwards.

The rule that makes this safe to enforce is here rather than in three view
files: a request may only be addressed to somebody who is actually on the
right desk. Without that check the field is free text, and "assigned to
whoever the browser said" is not an assignment.
"""
from .models import AdminUser

# Which roster a queue draws from. Admin approves rooms and item requests;
# IT Support works tickets. Same split as AdminUser.scope, named once.
DESK_SCOPE = {'admin': 'admin', 'it': 'it_support'}


def staff_for(desk):
    """-> the people who can be given work on this desk."""
    scope = DESK_SCOPE.get(desk)
    if not scope:
        return AdminUser.objects.none()
    return AdminUser.objects.filter(scope=scope).order_by('name', 'email')


def resolve(desk, raw_email):
    """-> (email, name) for a chosen person, or an error string.

    Required, by decision: every request is addressed to somebody from the
    moment it is raised. The cost of that is a request sitting with one
    person who is away, so the desk's own people and the super admin can
    hand it on -- see the reassign action on each queue.
    """
    email = str(raw_email or '').strip().lower()
    if not email:
        return 'Choose who this should go to.'
    person = staff_for(desk).filter(email__iexact=email).first()
    if not person:
        # Deliberately not "no such person": the browser sent an address
        # that is not on this desk, which is either a stale roster in a page
        # somebody left open, or someone editing the request by hand.
        return 'That person is not on this desk. Pick somebody from the list.'
    return person.email, (person.name or person.email.split('@')[0])


def visible_to(qs, role, email):
    """Narrow a queue to what this caller may see.

    Staff see their own assignments only -- a deliberate choice, so each
    person's screen is their own work and nobody else's. The super admin
    oversees everybody and sees all of it.
    """
    if role == 'super_admin':
        return qs
    return qs.filter(assigned_to_email__iexact=email)
