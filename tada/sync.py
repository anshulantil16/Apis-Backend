"""Fill the TA/DA directory from the HRMS master, instead of a spreadsheet.

Everybody who works here is already in the portal directory, synced nightly
from Pocket HRMS -- name, email, designation, department, grade, location.
Re-typing all of that into a template and importing it meant maintaining the
same people twice and letting the two drift apart.

What this does NOT do is take over TA/DA's own fields. Three of them decide
things HRMS knows nothing about:

  role       who approves what. HRMS has no notion of "this person is the
             one who signs off Finance".
  level      M1-M7 / E1-E4. This is the band the allowance matrices are keyed
             on -- it decides money. HRMS sends a grade ("M4", "O2", "W1",
             "C1"), which is not the same alphabet: only some of them even
             look like a TA/DA level, and guessing the rest would quietly
             pay people the wrong rate.
  hq_city    where somebody is based for the purposes of an ex-HQ claim,
             which is not always the office HRMS has them attached to.

So: details HRMS owns are refreshed every run. TA/DA's own fields are filled
in only when they are empty and HRMS has something credible to put there,
and are never overwritten once a person has set them.

The approver
------------
reporting_manager_id is what routes a request to whoever approves it, and
it is the field this file has to be most careful with. HRMS does not send a
reporting manager for most people, so it is taken from the first of these
that HRMS actually has (APPROVER_CHAIN below):

    reporting manager -> HOD -> leave approver -> leave recommender

all four of which are columns in the master already. Which one was used is
recorded in approver_source, so the admin table shows "via HOD" rather than
a code that appeared from nowhere, and a person can override it.

Two rules make this safe:

  * it is only ever written into a blank. An approver somebody set by hand
    is who the request goes to, and is not ours to replace.
  * the code has to belong to somebody in the directory. A code HRMS has
    for a person who is not here routes the request at nobody, which looks
    identical to "approved by nobody" until somebody chases their money.
"""
from accounts.models import PortalUser
from .models import TadaUser

# Levels the allowance matrices are keyed on. A grade is only copied into
# `level` when it is already one of these -- see the module docstring.
VALID_LEVELS = ({f'M{n}' for n in range(1, 8)} | {f'E{n}' for n in range(1, 5)})

# Straight from HRMS every run; HRMS is the source of truth for these.
FROM_MASTER = ('name', 'email', 'designation', 'department', 'grade')

# Also HRMS's, but only written when the master actually has a value, so a
# thin feed one night does not wipe the few people it is filled in for.
FROM_MASTER_IF_SET = ('hod_id', 'hod_name')

# Filled when empty, never overwritten. See the docstring.
FILL_ONLY = ('level', 'hq_city')

# In order. The first one the master has a usable code for becomes the
# approver, and the label is what the admin screen shows beside the name.
APPROVER_CHAIN = (
    ('reporting_manager_code', 'manager'),
    ('hod_code', 'hod'),
    ('leave_approver_code', 'leave approver'),
    ('leave_recommender_code', 'leave recommender'),
)


def _level_from_grade(grade):
    """A TA/DA level, only when the HRMS grade already is one."""
    g = (grade or '').strip().upper().replace(' ', '')
    return g if g in VALID_LEVELS else ''


def _approver_from_master(p, known):
    """-> (employee code, which column it came from), or ('', '').

    `known` is every employee code that will be in the directory after this
    run. A code outside it is skipped rather than stored: routing a claim to
    somebody who is not here is worse than leaving it unrouted, because an
    unrouted one is visible on the admin screen and the other is not.
    """
    own = (p.employee_code or '').strip()
    for attr, label in APPROVER_CHAIN:
        code = (getattr(p, attr, '') or '').strip()
        # Nobody approves their own travel, whatever the master says.
        if code and code != own and code in known:
            return code, label
    return '', ''


def sync_from_master(only_active=True):
    """-> a summary dict. Creates and refreshes TadaUser rows from PortalUser."""
    created = updated = untouched = 0
    no_level = []          # in the directory, but nobody can claim until a level is set
    deactivated = 0
    routed = {}            # which column each newly-filled approver came from

    people = list(PortalUser.objects.all() if not only_active
                  else PortalUser.objects.filter(is_active=True))

    # Every code that will exist in the directory after this run: the people
    # being synced now, plus anybody already in TA/DA. Built once.
    known = {(p.employee_code or '').strip() for p in people}
    known.update(TadaUser.objects.values_list('employee_id', flat=True))
    known.discard('')

    for p in people:
        code = (p.employee_code or '').strip()
        if not code:
            continue

        incoming = {
            'name': p.name or code,
            'email': p.email or '',
            'designation': p.designation or '',
            'department': p.department or '',
            'grade': (p.grade or '')[:20],
        }
        from_hrms_if_set = {
            'hod_id': (p.hod_code or '').strip(),
            'hod_name': (p.hod_name or '').strip(),
        }
        fill = {
            'level': _level_from_grade(p.grade),
            'hq_city': p.location or '',
        }
        approver, source = _approver_from_master(p, known)

        u = TadaUser.objects.filter(employee_id=code).first()
        if u is None:
            TadaUser.objects.create(
                employee_id=code, is_active=True,
                reporting_manager_id=approver,
                approver_source=source,
                **incoming,
                **{k: v for k, v in from_hrms_if_set.items() if v},
                **fill,
            )
            created += 1
            if approver:
                routed[source] = routed.get(source, 0) + 1
            if not fill['level']:
                no_level.append(f'{code} {incoming["name"]}'.strip())
            continue

        changed = False
        for key, value in incoming.items():
            if getattr(u, key) != value:
                setattr(u, key, value)
                changed = True
        for key in FROM_MASTER_IF_SET:
            value = from_hrms_if_set[key]
            if value and getattr(u, key) != value:
                setattr(u, key, value)
                changed = True
        for key in FILL_ONLY:
            # Only ever writes into a blank. A level somebody set by hand is
            # what the allowance is paid on, and is not ours to replace.
            if not getattr(u, key) and fill.get(key):
                setattr(u, key, fill[key])
                changed = True
        # Same rule, and the one that matters most: an approver somebody
        # chose stays, and keeps whatever source it was recorded under.
        if not u.reporting_manager_id and approver:
            u.reporting_manager_id = approver
            u.approver_source = source
            routed[source] = routed.get(source, 0) + 1
            changed = True
        # Somebody who has left the company cannot raise a claim. Reactivation
        # is left to a person: coming back is rarer than a bad feed.
        if not p.is_active and u.is_active:
            u.is_active = False
            deactivated += 1
            changed = True
        if changed:
            u.save()
            updated += 1
        else:
            untouched += 1
        if not u.level:
            no_level.append(f'{code} {u.name}'.strip())

    return {
        'created': created,
        'updated': updated,
        'unchanged': untouched,
        'deactivated': deactivated,
        'total': TadaUser.objects.count(),
        'needs_level': no_level,
        'routed': routed,
        'no_approver': TadaUser.objects.filter(is_active=True,
                                               reporting_manager_id='').count(),
    }
