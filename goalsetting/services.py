"""The rules that must hold no matter which screen calls them.

Saving a goal sheet and moving it along the workflow both happen from three
different places (employee, manager, HOD), so they live here once. A rule
enforced in a view is a rule that holds only for the screens that remembered
to call it.
"""
from django.db import transaction
from django.utils import timezone

from .models import CATEGORIES, GoalKPI, GoalPlan, KRA, PlanEvent, PlanVersion

# A KPI is worth keeping only if someone typed something into it. Rows that are
# entirely blank are the empty row the form always shows at the bottom, not a
# goal, and saving them would leave phantom KPIs on every plan.
KPI_FIELDS = ('metric', 'frequency', 'unit_of_measurement',
              'parameter_type', 'data_source', 'target_value')


def _f(value):
    """A weightage as a number. Blank, None and junk all mean zero."""
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def _blank_kpi(k):
    if _f(k.get('weightage')) != 0:
        return False
    return not any(str(k.get(f) or '').strip() for f in KPI_FIELDS)


@transaction.atomic
def save_kras(plan, rows):
    """Replace the plan's KRA/KPI tree with what was posted.

    Rewritten wholesale rather than patched row by row. A manager here may
    delete a KRA, reorder them or move a KPI to another KRA, and reconciling
    that by id turns into guesswork about intent; the snapshot in PlanVersion
    is what preserves the old state, so nothing is lost by rebuilding.
    """
    plan.kras.all().delete()

    for i, row in enumerate(rows or []):
        kpis = [k for k in (row.get('kpis') or []) if not _blank_kpi(k)]
        title = str(row.get('title') or '').strip()

        # A KRA with no title and no measures is an empty form row.
        if not title and not kpis:
            continue

        kra = KRA.objects.create(
            plan=plan,
            category=str(row.get('category') or '').strip(),
            title=title,
            description=str(row.get('description') or '').strip(),
            order=i,
        )
        GoalKPI.objects.bulk_create([
            GoalKPI(
                kra=kra,
                metric=str(k.get('metric') or '').strip(),
                weightage=_f(k.get('weightage')),
                frequency=str(k.get('frequency') or '').strip(),
                unit_of_measurement=str(k.get('unit_of_measurement') or '').strip(),
                parameter_type=str(k.get('parameter_type') or '').strip(),
                data_source=str(k.get('data_source') or '').strip(),
                target_value=str(k.get('target_value') or '').strip(),
                order=j,
            ) for j, k in enumerate(kpis)
        ])
    return plan


def readiness(plan):
    """Why this plan cannot move on yet, in the order a person would fix them.

    Returned as a list rather than a single message so the form can show every
    problem at once. Sending someone back four times for four blank fields is
    how a tool earns a reputation for being painful.
    """
    problems = []
    kras = list(plan.kras.all())

    if not kras:
        return ['Add at least one KRA before sending this on.']

    for kra in kras:
        if not kra.title.strip():
            problems.append(f'A KRA under {kra.category or "an unnamed category"} has no title.')
        if not kra.kpis.exists():
            problems.append(f'"{kra.title or "Untitled KRA"}" has no KPI under it.')
        for kpi in kra.kpis.all():
            missing = [label for field, label in (
                ('metric', 'KPI / Metric'), ('frequency', 'Frequency'),
                ('unit_of_measurement', 'Unit of Measurement'),
                ('parameter_type', 'Parameter Direction'),
                ('data_source', 'Data Source'), ('target_value', 'Plan / Target'),
            ) if not str(getattr(kpi, field) or '').strip()]
            if missing:
                problems.append(
                    f'"{kpi.metric or "An unnamed KPI"}" under "{kra.title or "Untitled KRA"}" '
                    f'is missing: {", ".join(missing)}.')
            if kpi.weightage <= 0:
                problems.append(
                    f'"{kpi.metric or "An unnamed KPI"}" has no weightage.')

    total = plan.total_weightage
    if round(total, 2) != 100.0:
        over = total > 100
        problems.append(
            f'Total weightage is {total:g}% - it must be exactly 100%. '
            f'{"Reduce" if over else "Add"} {abs(round(100 - total, 2)):g}%.')

    return problems


# Where each hand-off sends the plan, and who is allowed to make it. Keeping
# this as data means the workflow can be read in one place instead of being
# inferred from branches scattered across three view files.
TRANSITIONS = {
    # action:        (from,                    to,                   by)
    'submit':        (('draft', 'returned'),    'submitted',          'employee'),
    'to_hod':        (('submitted',),           'with_hod',           'manager'),
    'manager_return': (('submitted',),          'returned',           'manager'),
    'to_employee':   (('with_hod',),            'awaiting_employee',  'hod'),
    'hod_return':    (('with_hod',),            'returned',           'hod'),
    'accept':        (('awaiting_employee',),   'accepted',           'employee'),
    'employee_return': (('awaiting_employee',), 'returned',           'employee'),
}

_STAMP = {
    'submitted': 'submitted_at',
    'with_hod': 'manager_acted_at',
    'awaiting_employee': 'hod_acted_at',
    'accepted': 'accepted_at',
}

# Only these actions are a commitment to the goals as they stand. A return is a
# request for changes, so it must not be blocked by the sheet being incomplete
# - that is usually the very reason it is being sent back.
_NEEDS_COMPLETE = {'submit', 'to_hod', 'to_employee'}


class WorkflowError(Exception):
    def __init__(self, message, problems=None, status=400):
        super().__init__(message)
        self.message = message
        self.problems = problems or []
        self.status = status


@transaction.atomic
def advance(plan, action, *, role, name='', employee_id='', note=''):
    """Move a plan to its next stage, recording a version as it goes."""
    rule = TRANSITIONS.get(action)
    if not rule:
        raise WorkflowError(f'Unknown action "{action}".')

    allowed_from, target, actor_role = rule

    if role != actor_role:
        raise WorkflowError(f'Only the {actor_role} can do that.', status=403)

    if plan.status not in allowed_from:
        raise WorkflowError(
            f'This plan is "{plan.get_status_display()}", so that step does not apply to it. '
            f'Someone may have moved it already - reload to see where it is.',
            status=409)

    if not plan.cycle.accepts_edits and action != 'accept':
        raise WorkflowError(
            f'The {plan.cycle.name} cycle is {plan.cycle.get_status_display().lower()}, '
            f'so plans cannot be moved. Contact the admin.', status=403)

    if action in _NEEDS_COMPLETE:
        problems = readiness(plan)
        if problems:
            raise WorkflowError('This goal sheet is not complete yet.', problems)

    plan.status = target
    if action == 'submit':
        plan.employee_note = note or plan.employee_note
    elif action in ('to_hod', 'manager_return'):
        plan.manager_note = note or plan.manager_note
    elif action in ('to_employee', 'hod_return'):
        plan.hod_note = note or plan.hod_note
    elif action in ('accept', 'employee_return'):
        plan.employee_acceptance_note = note or plan.employee_acceptance_note

    stamp = _STAMP.get(target)
    if stamp:
        setattr(plan, stamp, timezone.now())
    plan.save()

    PlanVersion.record(plan, role=role, name=name, employee_id=employee_id,
                       action=action, note=note)
    PlanEvent.objects.create(plan=plan, actor_role=role, actor_name=name,
                             action=action, note=note)
    return plan


def seed_categories(plan):
    """Give a brand-new plan one empty KRA per category, so the employee opens
    a sheet with the shape of the task rather than a blank page."""
    if plan.kras.exists():
        return
    for i, cat in enumerate(CATEGORIES):
        KRA.objects.create(plan=plan, category=cat, title='', order=i)


def get_or_create_plan(employee, cycle):
    plan, created = GoalPlan.objects.get_or_create(employee=employee, cycle=cycle)
    if created:
        seed_categories(plan)
    return plan
