"""Passing a piece of work to somebody else.

One module for all three queues, for the reason assignment.py exists: this
rule was going to be written three times -- once in each action view -- and
the three copies would have drifted the first time one of them was fixed.

What a transfer is, and is not:

- it moves the *work*, not the decision. A transferred request keeps its
  status, its remarks and its place in the queue; the only thing that changes
  is whose desk it is on. Approving and rejecting stay what they were;
- it stays on one desk. An IT ticket cannot be handed to an admin -- they
  cannot approve it, so it would vanish into a queue nobody works;
- it needs a reason, which the person receiving it is owed. "Passed to Sana"
  answers nothing. "Sana looks after the VPN" is the record;
- and it cannot be a no-op. Handing something to the person who already has
  it writes a trail entry that says nothing happened.
"""
from .assignment import SCOPE_DESK, resolve
from .models import Handover
from .people import name_map


def transfer(kind, obj, desk, raw_to, actor_email, actor_role, reason):
    """Move `obj` to somebody else on `desk`. -> Handover, or an error string.

    The caller checks the status gate first: what counts as "still passable"
    differs per queue and is that queue's business, not this one's. Everything
    that is the same for all three is here.
    """
    reason = str(reason or '').strip()
    if not reason:
        return 'Say why you are passing this on — whoever gets it needs to know.'

    chosen = resolve(desk, raw_to)
    if isinstance(chosen, str):
        return chosen
    to_email, to_name = chosen

    was = (obj.assigned_to_email or '').strip().lower()
    if was == to_email.lower():
        return ('That is already whose desk this is on. Pick somebody else.')

    # Who may pass it on: whoever it currently sits with, anybody else on the
    # same desk (they share the queue, and unclaimed work is everybody's), and
    # the super admin, who oversees both. An employee may not -- they raised
    # it, they do not run it.
    actor = (actor_email or '').strip().lower()
    if actor_role != 'super_admin':
        if SCOPE_DESK.get(actor_role) != desk:
            return 'Only somebody on this desk can pass this on.'

    record = Handover.objects.create(
        kind=kind, object_id=obj.id,
        from_email=obj.assigned_to_email or '',
        from_name=obj.assigned_to_name or '',
        to_email=to_email, to_name=to_name,
        by_email=actor, by_role=actor_role or '',
        reason=reason[:300],
    )
    obj.assigned_to_email = to_email
    obj.assigned_to_name = to_name
    # Only the two columns, so a transfer cannot disturb a status or a remark
    # somebody else changed between this row being read and saved.
    obj.save(update_fields=['assigned_to_email', 'assigned_to_name', 'updated_at'])
    return record


def trail_for(kind, ids):
    """-> {object_id: [hop, ...]} for a page of rows, in two queries.

    Per-row lookups would be one query each, and these lists are read on
    every queue screen.
    """
    ids = [i for i in ids if i]
    if not ids:
        return {}
    rows = list(Handover.objects.filter(kind=kind, object_id__in=ids))
    # By the name the company knows them by, like every other list.
    proper = name_map([r.from_email for r in rows] + [r.to_email for r in rows]
                      + [r.by_email for r in rows])

    def who(email, stored):
        return proper.get((email or '').lower()) or stored or email or ''

    out = {}
    for r in rows:
        out.setdefault(r.object_id, []).append({
            'from_email': r.from_email,
            'from': who(r.from_email, r.from_name),
            'to_email': r.to_email,
            'to': who(r.to_email, r.to_name),
            'by_email': r.by_email,
            'by': who(r.by_email, ''),
            'reason': r.reason,
            'at': r.created_at.isoformat(),
        })
    return out
