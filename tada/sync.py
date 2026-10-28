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

The same rule covers reporting_manager_id, and it is the one that matters
most. It is what routes a request to an approver. HRMS has a reporting
manager for 3 of 717 people, so copying the column over wholesale would
blank it for everyone it is currently set for and leave their travel
requests with nobody to approve them.
"""
from accounts.models import PortalUser
from .models import TadaUser

# Levels the allowance matrices are keyed on. A grade is only copied into
# `level` when it is already one of these -- see the module docstring.
VALID_LEVELS = ({f'M{n}' for n in range(1, 8)} | {f'E{n}' for n in range(1, 5)})

# Straight from HRMS every run; HRMS is the source of truth for these.
FROM_MASTER = ('name', 'email', 'designation', 'department', 'grade')

# Filled when empty, never overwritten. See the docstring.
FILL_ONLY = ('level', 'hq_city', 'reporting_manager_id')


def _level_from_grade(grade):
    """A TA/DA level, only when the HRMS grade already is one."""
    g = (grade or '').strip().upper().replace(' ', '')
    return g if g in VALID_LEVELS else ''


def sync_from_master(only_active=True):
    """-> a summary dict. Creates and refreshes TadaUser rows from PortalUser."""
    created = updated = untouched = 0
    no_level = []          # in the directory, but nobody can claim until a level is set
    deactivated = 0

    people = PortalUser.objects.all()
    if only_active:
        people = people.filter(is_active=True)

    seen = set()
    for p in people:
        code = (p.employee_code or '').strip()
        if not code:
            continue
        seen.add(code)

        incoming = {
            'name': p.name or code,
            'email': p.email or '',
            'designation': p.designation or '',
            'department': p.department or '',
            'grade': (p.grade or '')[:20],
        }
        fill = {
            'level': _level_from_grade(p.grade),
            'hq_city': p.location or '',
            'reporting_manager_id': p.reporting_manager_code or '',
        }

        u = TadaUser.objects.filter(employee_id=code).first()
        if u is None:
            TadaUser.objects.create(
                employee_id=code, is_active=True,
                **incoming,
                **{k: v for k, v in fill.items()},
            )
            created += 1
            if not fill['level']:
                no_level.append(f'{code} {incoming["name"]}'.strip())
            continue

        changed = False
        for key, value in incoming.items():
            if getattr(u, key) != value:
                setattr(u, key, value)
                changed = True
        for key in FILL_ONLY:
            # Only ever writes into a blank. A level somebody set by hand is
            # what the allowance is paid on; an approver somebody set by hand
            # is who the request goes to. Neither is ours to replace.
            if not getattr(u, key) and fill.get(key):
                setattr(u, key, fill[key])
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
    }
