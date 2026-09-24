"""The company's employee directory, as AdminPulse sees it.

The master is accounts.PortalUser -- the intranet's own directory, synced from
HRMS. AdminPulse kept a second copy, uploaded from a spreadsheet every time
somebody joined or left, and the two disagreed the moment either changed.

So this pulls from that one instead. What it must never do is treat the sync
as the whole truth: people get added here by hand precisely because they are
NOT in HRMS -- a contractor, a joiner not yet on the system, the facilities
number that raises tickets for the building. Those rows carry source='manual'
and a sync leaves them alone.
"""
from django.db import transaction
from django.utils import timezone

from accounts.models import PortalUser

from .models import AdminUser, Employee


def sync_from_directory():
    """Refresh every directory-sourced row from PortalUser.

    -> {'created', 'updated', 'deactivated', 'skipped_no_email'}
    """
    seen = set()
    created = updated = skipped = 0

    people = PortalUser.objects.all().only(
        'employee_code', 'name', 'email', 'department', 'designation',
        'location', 'is_active')

    with transaction.atomic():
        for p in people:
            email = (p.email or '').strip().lower()
            if not email:
                # Identity here is the email address -- a row without one
                # cannot be matched to anybody signing in, so importing it
                # would just be a name nobody could ever be.
                skipped += 1
                continue
            seen.add(email)

            fields = {
                'employee_code': (p.employee_code or '')[:50],
                'name': (p.name or email.split('@')[0])[:200],
                'department': (p.department or '')[:150],
                'designation': (p.designation or '')[:150],
                'location': (p.location or '')[:150],
                'is_active': bool(p.is_active),
                'source': 'directory',
                'synced_at': timezone.now(),
            }

            row = Employee.objects.filter(email=email).first()
            if row is None:
                Employee.objects.create(email=email, **fields)
                created += 1
                continue

            # A row somebody added by hand is left as it is, except that the
            # directory now knows this person: adopt it rather than keeping a
            # duplicate that the next sync would fight over.
            for k, v in fields.items():
                setattr(row, k, v)
            row.save()
            updated += 1

        # People who have left: marked inactive, never deleted. Their name is
        # on tickets they raised and work done for them, and a directory that
        # forgets them makes that history unreadable.
        gone = (Employee.objects.filter(source='directory', is_active=True)
                .exclude(email__in=seen))
        deactivated = gone.count()
        gone.update(is_active=False)

    _mirror_roles()
    return {'created': created, 'updated': updated,
            'deactivated': deactivated, 'skipped_no_email': skipped}


def _mirror_roles():
    """Keep Employee.role showing who holds Admin / IT Support.

    A display mirror, not the source of truth -- resolve_role() always reads
    AdminUser. Without this the directory shows everybody as an employee right
    after a sync, including the people who run it.
    """
    scopes = {a.email.lower(): a.scope for a in AdminUser.objects.all()}
    for row in Employee.objects.all().only('id', 'email', 'role'):
        want = scopes.get(row.email.lower(), 'employee')
        if row.role != want:
            Employee.objects.filter(id=row.id).update(role=want)


def in_directory(email):
    """Is this address a real person here? -> the Employee row, or None.

    Used by sign-in. The rule was "anything @apisindia.com", which let in an
    address belonging to nobody and, more to the point, shut out most of the
    company: of 664 people on the directory only 169 have a company address,
    the rest being personal ones recorded in HRMS.
    """
    email = (email or '').strip().lower()
    if not email:
        return None
    return Employee.objects.filter(email=email, is_active=True).first()
