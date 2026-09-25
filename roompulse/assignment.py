"""Who a request belongs to, and who can see it.

Each desk is two or three people. Everything they handled used to land in one
pile all of them saw, so nothing could say which of them had dealt with what.
Now every request names one person when it is raised.

**All the visibility rules live here, in one function per queue.** They were
written inline in three list views, one `elif` at a time, and each fix broke
something in a branch nobody re-read -- IT's own logged work disappeared from
IT's screen because the assignment filter did not know that a job you logged
is not a job anyone assigned you. One place, four roles, stated once:

    super admin  oversees both desks and sees everything
    IT Support   their tickets, the IT work log, and anything unclaimed
    Admin        their rooms and items, the Admin work log, unclaimed work
    employee     what they asked for, and never either work log

Two rules carry most of the weight. A **logged job is not assigned to
anybody** -- it has already happened, so it belongs to its desk rather than
to a queue, and any filter about assignment has to leave it alone. And
**unassigned work belongs to nobody**, so it is shown to the whole desk: a
row nobody owns otherwise appears on no screen at all, which is how requests
raised before assignment existed silently stopped being anybody's job.
"""
from django.db.models import Q

from .models import AdminUser

# Which roster a queue draws from. Admin approves rooms and item requests;
# IT Support works tickets. The same split as AdminUser.scope, named once.
DESK_SCOPE = {'admin': 'admin', 'it': 'it_support'}
SCOPE_DESK = {'admin': 'admin', 'it_support': 'it'}


def staff_for(desk):
    """-> the people who can be given work on this desk."""
    scope = DESK_SCOPE.get(desk)
    if not scope:
        return AdminUser.objects.none()
    return AdminUser.objects.filter(scope=scope).order_by('name', 'email')


def desk_of(email):
    """-> 'it' | 'admin' | '' for whoever this is."""
    row = AdminUser.objects.filter(email__iexact=email).first()
    return SCOPE_DESK.get(row.scope, '') if row else ''


def resolve(desk, raw_email):
    """-> (email, name) for a chosen person, or an error string.

    Required, by decision: every request is addressed to somebody from the
    moment it is raised. The name is returned alongside the address and
    stored with it, so if that person later leaves the roster the record of
    who dealt with this still reads.
    """
    email = str(raw_email or '').strip().lower()
    if not email:
        return 'Choose who this should go to.'
    person = staff_for(desk).filter(email__iexact=email).first()
    if not person:
        # Deliberately not "no such person": the browser sent an address that
        # is not on this desk, which is either a stale roster in a page left
        # open, or somebody editing the request by hand.
        return 'That person is not on this desk. Pick somebody from the list.'
    return person.email, (person.name or person.email.split('@')[0])


# ── what each role sees ──────────────────────────────────────────────────

def tickets_for(qs, role, email):
    """The IT ticket queue, as one person sees it."""
    if role == 'super_admin':
        return qs
    if role == 'it_support':
        return qs.filter(
            # theirs
            Q(assigned_to_email__iexact=email)
            # the desk's own record of work nobody raised -- not assigned to
            # anyone, because it was done before it was written down
            | Q(origin='logged', desk='it')
            # and anything still unclaimed, so nothing sits owned by nobody
            | Q(origin='requested', assigned_to_email=''))
    if role == 'admin':
        # An admin is not IT triage: they do not get the ticket queue. They
        # get Admin's own work log, which they write, and whatever they
        # personally raised.
        return qs.filter(Q(origin='logged', desk='admin')
                         | Q(requested_by_email__iexact=email))
    # An employee: their own, and never either work log -- that is a record
    # of what the team did, not correspondence with them.
    return qs.filter(requested_by_email__iexact=email).exclude(origin='logged')


def admin_queue_for(qs, role, email):
    """Room bookings and item requests, as one person sees them."""
    if role == 'super_admin':
        return qs
    if role == 'admin':
        return qs.filter(Q(assigned_to_email__iexact=email)
                         | Q(assigned_to_email='')
                         # what they asked for themselves, which is theirs to
                         # see whoever they sent it to
                         | Q(requested_by_email__iexact=email))
    # Everybody else, IT Support included, is an ordinary requester here.
    return qs.filter(requested_by_email__iexact=email)


def raised_by(qs, email):
    """What one person asked for -- the "My Requests" question.

    Replaces the visibility filter rather than narrowing it. A request you
    raised and addressed to a colleague satisfies neither "assigned to me"
    nor "on my desk", so the two together answered "nothing" for the
    commonest case there is: your own screen, showing none of your own work.
    """
    return qs.filter(requested_by_email__iexact=email)
