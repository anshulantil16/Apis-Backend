"""Goal Setting Hub API.

Kept in one file because the product is one workflow. Splitting it by role the
way Appraisal Hub does put the same plan's rules in three places and made the
sequence hard to follow; here the whole life of a goal sheet reads top to
bottom.
"""
import secrets
from datetime import timedelta

import pandas as pd
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (ADMIN_BOOTSTRAP_EMAIL, CATEGORIES, EmployeeProfile,
                     FREQUENCY_OPTIONS, GoalCycle, GoalPlan, KRA, OTPToken,
                     PlanEvent, PlanVersion)
from .serializers import (CycleSerializer, EmployeeSerializer, PlanSerializer,
                          PlanSummarySerializer)
from .services import (WorkflowError, advance, force_status, get_or_create_plan,
                       readiness, record_admin_edit, save_kras)


class GSView(APIView):
    """Opts out of the project-wide JWT authentication.

    This product signs people in with its own employee-id + OTP, the way
    Appraisal Hub does, so SimpleJWT must not try to parse the request and
    reject it before any of this code runs.
    """
    authentication_classes = []
    permission_classes = []


def _plans():
    """One query shape for every plan read - the tree is always needed."""
    return (GoalPlan.objects
            .select_related('employee', 'cycle')
            .prefetch_related(Prefetch('kras', queryset=KRA.objects.prefetch_related('kpis')),
                              'versions', 'events'))


def _mask(email):
    if not email or '@' not in email:
        return '***@***.***'
    local, domain = email.split('@', 1)
    if len(local) <= 2:
        return f'{"*" * len(local)}@{domain}'
    return f'{local[0]}{"*" * (len(local) - 2)}{local[-1]}@{domain}'


def _dev_login():
    return bool(getattr(settings, 'PORTAL_DEV_LOGIN', False))


# --- sign in -----------------------------------------------------------------

class SendOTPView(GSView):
    def post(self, request):
        emp_id = str(request.data.get('employee_id') or '').strip()
        if not emp_id:
            return Response({'error': 'Enter your Employee ID.'}, status=400)

        emp = EmployeeProfile.objects.filter(employee_id__iexact=emp_id, is_active=True).first()
        if not emp:
            return Response({'error': 'That Employee ID is not on the goal-setting list. '
                                      'Contact your admin.'}, status=404)
        if not emp.email:
            return Response({'error': 'There is no email address on file for this ID, so a code '
                                      'cannot be sent. Contact your admin.'}, status=400)

        OTPToken.objects.filter(employee=emp, is_used=False).delete()
        code = f'{secrets.randbelow(1_000_000):06d}'
        OTPToken.objects.create(employee=emp, otp_code=code,
                                expires_at=timezone.now() + timedelta(minutes=5))

        body = {'message': 'Code sent.', 'masked_email': _mask(emp.email), 'name': emp.name}

        # Same reasoning as the portal: a developer machine has no SMTP, so
        # without this nobody could sign in locally to test the product.
        if _dev_login():
            print(f'[goalsetting] dev code for {emp.employee_id}: {code}')
            return Response({**body, 'dev_otp': code})

        try:
            send_mail(
                subject='Your Goal Setting sign-in code',
                message=(f'Hi {emp.name},\n\nYour code for APIS Goal Setting is:\n\n    {code}\n\n'
                         f'It is valid for 5 minutes and can be used once.\n\n- APIS'),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[emp.email], fail_silently=False)
        except Exception as e:
            return Response({'error': f'Could not send the code: {e}'}, status=500)
        return Response(body)


class VerifyOTPView(GSView):
    def post(self, request):
        emp_id = str(request.data.get('employee_id') or '').strip()
        code = str(request.data.get('otp') or '').strip()
        if not emp_id or not code:
            return Response({'error': 'Employee ID and code are both required.'}, status=400)

        emp = EmployeeProfile.objects.filter(employee_id__iexact=emp_id, is_active=True).first()
        if not emp:
            return Response({'error': 'Employee not found.'}, status=404)

        token = OTPToken.objects.filter(employee=emp, is_used=False, otp_code=code).first()
        if not token:
            return Response({'error': 'That code is not right. Please check and try again.'}, status=400)
        if not token.is_valid():
            return Response({'error': 'That code has expired. Please request a new one.'}, status=400)

        token.is_used = True
        token.save(update_fields=['is_used'])
        return Response({'message': 'Signed in.', 'employee': EmployeeSerializer(emp).data})


class AdminOTPView(GSView):
    """Admin sign-in. The code always goes to the bootstrap address, so the
    product is usable before a single employee has been uploaded."""

    def post(self, request):
        code = f'{secrets.randbelow(1_000_000):06d}'
        emp, _ = EmployeeProfile.objects.get_or_create(
            employee_id='GS-ADMIN',
            defaults={'name': 'Goal Setting Admin', 'email': ADMIN_BOOTSTRAP_EMAIL,
                      'user_type': 'admin', 'designation': 'Administrator'})
        if emp.email != ADMIN_BOOTSTRAP_EMAIL or emp.user_type != 'admin':
            emp.email, emp.user_type = ADMIN_BOOTSTRAP_EMAIL, 'admin'
            emp.save(update_fields=['email', 'user_type'])

        OTPToken.objects.filter(employee=emp, is_used=False).delete()
        OTPToken.objects.create(employee=emp, otp_code=code,
                                expires_at=timezone.now() + timedelta(minutes=5))

        body = {'message': 'Code sent.', 'masked_email': _mask(ADMIN_BOOTSTRAP_EMAIL)}
        if _dev_login():
            print(f'[goalsetting] dev admin code: {code}')
            return Response({**body, 'dev_otp': code})
        try:
            send_mail(subject='Your Goal Setting admin code',
                      message=f'Your admin sign-in code for APIS Goal Setting is:\n\n    {code}\n\n'
                              f'Valid for 5 minutes.',
                      from_email=settings.DEFAULT_FROM_EMAIL,
                      recipient_list=[ADMIN_BOOTSTRAP_EMAIL], fail_silently=False)
        except Exception as e:
            return Response({'error': f'Could not send the code: {e}'}, status=500)
        return Response(body)


class AdminVerifyView(GSView):
    def post(self, request):
        code = str(request.data.get('otp') or '').strip()
        emp = EmployeeProfile.objects.filter(employee_id='GS-ADMIN').first()
        token = OTPToken.objects.filter(employee=emp, is_used=False, otp_code=code).first() if emp else None
        if not token or not token.is_valid():
            return Response({'error': 'That code is not right or has expired.'}, status=400)
        token.is_used = True
        token.save(update_fields=['is_used'])
        return Response({'message': 'Signed in.', 'employee': EmployeeSerializer(emp).data})


# --- reference data ----------------------------------------------------------

class MetaView(GSView):
    """The form's vocabulary, so the frontend does not hard-code a second copy."""

    def get(self, request):
        return Response({
            'categories': CATEGORIES,
            'frequencies': FREQUENCY_OPTIONS,
            'statuses': [{'value': v, 'label': l} for v, l in GoalPlan.STATUS_CHOICES],
        })


class CycleListView(GSView):
    def get(self, request):
        qs = GoalCycle.objects.all()
        if request.query_params.get('open') == '1':
            qs = qs.filter(status='open')
        return Response(CycleSerializer(qs, many=True).data)

    def post(self, request):
        name = str(request.data.get('name') or '').strip()
        fy = str(request.data.get('fiscal_year') or '').strip()
        if not name or not fy:
            return Response({'error': 'A cycle needs a name and a fiscal year.'}, status=400)
        if GoalCycle.objects.filter(name__iexact=name, fiscal_year=fy).exists():
            return Response({'error': f'A cycle called "{name}" already exists for {fy}.'}, status=400)
        cycle = GoalCycle.objects.create(
            name=name, fiscal_year=fy,
            starts_on=request.data.get('starts_on') or None,
            ends_on=request.data.get('ends_on') or None,
            submission_deadline=request.data.get('submission_deadline') or None,
            status=request.data.get('status') or 'draft',
            created_by=str(request.data.get('created_by') or ''),
        )
        return Response(CycleSerializer(cycle).data, status=201)


class CycleDetailView(GSView):
    def patch(self, request, cycle_id):
        cycle = GoalCycle.objects.filter(id=cycle_id).first()
        if not cycle:
            return Response({'error': 'Cycle not found.'}, status=404)
        for field in ('name', 'fiscal_year', 'status', 'created_by'):
            if field in request.data:
                setattr(cycle, field, request.data[field])
        for field in ('starts_on', 'ends_on', 'submission_deadline'):
            if field in request.data:
                setattr(cycle, field, request.data[field] or None)
        cycle.save()
        return Response(CycleSerializer(cycle).data)


# --- the goal sheet ----------------------------------------------------------

class PlanView(GSView):
    """GET or save one employee's sheet for one cycle."""

    def get(self, request, employee_id, cycle_id):
        """Read a sheet. Only the employee's own visit may CREATE one.

        A reviewer opening a colleague's sheet used to call get_or_create,
        so merely looking at someone marked them as started: the admin's
        "not started" count fell every time a manager clicked a name. A read
        must not change what it is reporting on.
        """
        emp = EmployeeProfile.objects.filter(employee_id__iexact=employee_id).first()
        cycle = GoalCycle.objects.filter(id=cycle_id).first()
        if not emp or not cycle:
            return Response({'error': 'Employee or cycle not found.'}, status=404)

        plan = GoalPlan.objects.filter(employee=emp, cycle=cycle).first()
        if not plan:
            if request.query_params.get('role') != 'employee':
                return Response({'error': f'{emp.name} has not started a goal sheet for '
                                          f'{cycle.name} yet.', 'not_started': True}, status=404)
            plan = get_or_create_plan(emp, cycle)
        return Response(PlanSerializer(_plans().get(id=plan.id)).data)

    def post(self, request, employee_id, cycle_id):
        """Save the table. Who may save is decided by where the plan sits, not
        by which screen asked - that is what stops a manager editing a sheet
        that has already moved on to the HOD."""
        emp = EmployeeProfile.objects.filter(employee_id__iexact=employee_id).first()
        cycle = GoalCycle.objects.filter(id=cycle_id).first()
        if not emp or not cycle:
            return Response({'error': 'Employee or cycle not found.'}, status=404)

        role = str(request.data.get('role') or 'employee')
        plan = get_or_create_plan(emp, cycle)

        # An admin edits regardless of the cycle being locked, or of whose turn
        # it is. That is the point of the seat: the workflow exists to keep
        # ordinary users in sequence, not to leave someone stuck.
        if role != 'admin':
            if not cycle.accepts_edits:
                return Response({'error': f'The {cycle.name} cycle is '
                                          f'{cycle.get_status_display().lower()}, so it cannot be edited.'},
                                status=403)
            if not plan.may_edit(role):
                holder = dict(GoalPlan.STATUS_CHOICES).get(plan.status, plan.status)
                return Response({'error': f'This sheet is "{holder}", so it is not yours to edit '
                                          f'right now.'}, status=403)

        with transaction.atomic():
            save_kras(plan, request.data.get('kras'))
            plan.refresh_from_db()
            if role == 'admin':
                record_admin_edit(plan, name=str(request.data.get('actor_name') or 'Administrator'),
                                  note=str(request.data.get('note') or ''))
        plan.refresh_from_db()
        data = PlanSerializer(_plans().get(id=plan.id)).data
        data['problems'] = readiness(plan)
        return Response(data)


class PlanActionView(GSView):
    """Move a sheet along: submit, send on, send back, accept."""

    def post(self, request, plan_id):
        plan = _plans().filter(id=plan_id).first()
        if not plan:
            return Response({'error': 'Goal sheet not found.'}, status=404)

        role = str(request.data.get('role') or '')

        # The save and the hand-off are ONE transaction, deliberately.
        #
        # Saving whatever is on screen first means a reviewer's last edit is
        # never lost to the act of sending it on. But when they were separate,
        # a refused hand-off still left its edit written: a manager could post
        # an edit against a locked cycle, be told "the cycle is locked", and
        # have the change land anyway. A request that is refused must change
        # nothing, so the WorkflowError has to escape the atomic block to roll
        # the save back.
        try:
            with transaction.atomic():
                if request.data.get('kras') is not None and plan.may_edit(role):
                    save_kras(plan, request.data.get('kras'))
                    plan.refresh_from_db()
                advance(plan, str(request.data.get('action') or ''),
                        role=role,
                        name=str(request.data.get('actor_name') or ''),
                        employee_id=str(request.data.get('actor_employee_id') or ''),
                        note=str(request.data.get('note') or ''))
        except WorkflowError as e:
            plan.refresh_from_db()
            return Response({'error': e.message, 'problems': e.problems}, status=e.status)

        return Response(PlanSerializer(_plans().get(id=plan.id)).data)


class PlanDetailView(GSView):
    def get(self, request, plan_id):
        plan = _plans().filter(id=plan_id).first()
        if not plan:
            return Response({'error': 'Goal sheet not found.'}, status=404)
        return Response(PlanSerializer(plan).data)


class MyPlansView(GSView):
    def get(self, request, employee_id):
        qs = _plans().filter(employee__employee_id__iexact=employee_id)
        return Response(PlanSummarySerializer(qs, many=True).data)


# --- team views --------------------------------------------------------------

def _team_response(request, people, cycle_id):
    """A reviewer's list: everyone under them, with a sheet or a reason there
    is none. Showing people who have not started is the point - the gaps are
    what a manager is chasing."""
    rows = []
    for emp in people:
        qs = GoalPlan.objects.filter(employee=emp)
        if cycle_id:
            qs = qs.filter(cycle_id=cycle_id)
        plan = qs.order_by('-created_at').first()
        rows.append({
            'employee_id': emp.employee_id,
            'name': emp.name,
            'designation': emp.designation,
            'department': emp.department,
            'zone': emp.zone,
            'plan': PlanSummarySerializer(plan).data if plan else None,
        })
    return Response(rows)


class ManagerTeamView(GSView):
    def get(self, request, manager_id):
        if not EmployeeProfile.objects.filter(employee_id__iexact=manager_id).exists():
            return Response({'error': 'Manager not found.'}, status=404)
        team = EmployeeProfile.objects.filter(reporting_manager_id__iexact=manager_id, is_active=True)
        return _team_response(request, team, request.query_params.get('cycle_id'))


class HODTeamView(GSView):
    def get(self, request, hod_id):
        if not EmployeeProfile.objects.filter(employee_id__iexact=hod_id).exists():
            return Response({'error': 'HOD not found.'}, status=404)
        team = EmployeeProfile.objects.filter(hod_id__iexact=hod_id, is_active=True)
        return _team_response(request, team, request.query_params.get('cycle_id'))


# --- admin -------------------------------------------------------------------

COLUMN_MAP = {
    'employee_id': ['employee_id', 'emp_id', 'emp_code', 'employee_code', 'user_id'],
    'name': ['name', 'employee_name', 'user_name'],
    'email': ['email', 'email_id', 'email_address'],
    'phone': ['phone', 'phone_number', 'mobile', 'contact'],
    'designation': ['designation', 'role', 'title'],
    'department': ['department', 'dept', 'function'],
    'zone': ['zone', 'region'],
    'subzone': ['subzone', 'sub_zone', 'area'],
    'reporting_manager_id': ['reporting_manager_id', 'reporting_to', 'manager_id', 'manager'],
    'hod_id': ['hod_id', 'hod', 'head_of_department_id'],
    'user_type': ['user_type', 'type', 'level'],
    'joined_date': ['joined_date', 'joining_date', 'doj', 'date_of_joining'],
}


class EmployeeImportView(GSView):
    """Upload the employee master. Same column aliases Appraisal Hub accepts,
    so an admin can reuse a sheet they already have."""
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        f = request.FILES.get('file')
        if not f:
            return Response({'error': 'No file uploaded.'}, status=400)
        try:
            if f.name.lower().endswith('.csv'):
                try:
                    df = pd.read_csv(f)
                except UnicodeDecodeError:
                    f.seek(0)
                    df = pd.read_csv(f, encoding='latin1')
            else:
                df = pd.read_excel(f)
            df.columns = df.columns.astype(str).str.strip().str.lower().str.replace(' ', '_')
            df = df.fillna('')
        except Exception as e:
            return Response({'error': f'Could not read that file: {e}'}, status=400)

        def col(aliases):
            return next((a for a in aliases if a in df.columns), None)

        resolved = {field: col(aliases) for field, aliases in COLUMN_MAP.items()}
        if not resolved['employee_id'] or not resolved['name']:
            return Response({'error': 'The sheet needs at least an Employee ID column and a Name '
                                      'column. Found: ' + ', '.join(df.columns)}, status=400)

        created = updated = skipped_samples = 0
        errors = []
        for i, row in df.iterrows():
            def val(field):
                c = resolved[field]
                return str(row[c]).strip() if c and str(row[c]).strip() else ''

            emp_id = val('employee_id')
            name = val('name')
            if not emp_id:
                errors.append(f'Row {i + 2}: no Employee ID.')
                continue

            # The template ships filled-in SAMPLE- rows so it is obvious what a
            # row should look like. Skipping them here is what makes leaving
            # them in harmless - an example you must remember to delete is a
            # trap, not a help.
            if emp_id.upper().startswith('SAMPLE-'):
                skipped_samples += 1
                continue
            if not name:
                errors.append(f'Row {i + 2}: no name for {emp_id}.')
                continue

            raw_type = val('user_type').lower()
            if 'admin' in raw_type or raw_type == 'hr':
                user_type = 'admin'
            elif 'hod' in raw_type or 'head' in raw_type:
                user_type = 'hod'
            elif 'manager' in raw_type or 'mgr' in raw_type:
                user_type = 'manager'
            else:
                user_type = 'employee'

            joined = None
            if val('joined_date'):
                try:
                    import dateutil.parser
                    joined = dateutil.parser.parse(val('joined_date'), dayfirst=True).date()
                except Exception:
                    errors.append(f'Row {i + 2}: could not read the joining date '
                                  f'"{val("joined_date")}" - left blank.')

            _, was_created = EmployeeProfile.objects.update_or_create(
                employee_id=emp_id,
                defaults={'name': name, 'email': val('email'), 'phone': val('phone'),
                          'designation': val('designation'), 'department': val('department'),
                          'zone': val('zone'), 'subzone': val('subzone'),
                          'reporting_manager_id': val('reporting_manager_id'),
                          'hod_id': val('hod_id'), 'user_type': user_type,
                          'joined_date': joined, 'is_active': True})
            created += was_created
            updated += (not was_created)

        return Response({'created': created, 'updated': updated,
                         'skipped_samples': skipped_samples,
                         'errors': errors[:50], 'error_count': len(errors)})


class EmployeeTemplateView(GSView):
    """Hand back a filled-in-shape .xlsx for the admin to complete.

    Built on the fly from the same column list the importer uses, so the two
    cannot drift apart - a stale template fails at upload with a complaint
    about a column the person is sure they included.
    """

    def get(self, request):
        from django.http import HttpResponse
        from .template import build_template

        r = HttpResponse(
            build_template(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        r['Content-Disposition'] = 'attachment; filename="goal-setting-employees-template.xlsx"'
        return r


class EmployeeListView(GSView):
    def get(self, request):
        qs = EmployeeProfile.objects.all()
        q = str(request.query_params.get('q') or '').strip()
        if q:
            from django.db.models import Q
            qs = qs.filter(Q(name__icontains=q) | Q(employee_id__icontains=q)
                           | Q(department__icontains=q) | Q(designation__icontains=q))
        if request.query_params.get('type'):
            qs = qs.filter(user_type=request.query_params['type'])
        return Response(EmployeeSerializer(qs, many=True).data)


class EmployeeDetailView(GSView):
    def patch(self, request, employee_id):
        emp = EmployeeProfile.objects.filter(employee_id__iexact=employee_id).first()
        if not emp:
            return Response({'error': 'Employee not found.'}, status=404)
        for field in ('name', 'email', 'phone', 'designation', 'department', 'zone',
                      'subzone', 'reporting_manager_id', 'hod_id', 'user_type', 'is_active'):
            if field in request.data:
                setattr(emp, field, request.data[field])
        emp.save()
        return Response(EmployeeSerializer(emp).data)

    def delete(self, request, employee_id):
        emp = EmployeeProfile.objects.filter(employee_id__iexact=employee_id).first()
        if not emp:
            return Response({'error': 'Employee not found.'}, status=404)
        emp.is_active = False
        emp.save(update_fields=['is_active'])
        return Response({'message': f'{emp.name} deactivated.'})


class AllPlansView(GSView):
    def get(self, request):
        qs = _plans()
        if request.query_params.get('cycle_id'):
            qs = qs.filter(cycle_id=request.query_params['cycle_id'])
        if request.query_params.get('status'):
            qs = qs.filter(status=request.query_params['status'])
        return Response(PlanSummarySerializer(qs, many=True).data)


class OverviewView(GSView):
    """The admin's answer to "where is everyone?"."""

    def get(self, request):
        cycle_id = request.query_params.get('cycle_id')
        plans = GoalPlan.objects.all()
        if cycle_id:
            plans = plans.filter(cycle_id=cycle_id)

        people = EmployeeProfile.objects.filter(is_active=True).exclude(user_type='admin')
        by_status = {value: plans.filter(status=value).count()
                     for value, _ in GoalPlan.STATUS_CHOICES}

        return Response({
            'employees': people.count(),
            'managers': people.filter(user_type='manager').count(),
            'hods': people.filter(user_type='hod').count(),
            'plans': plans.count(),
            'not_started': max(people.count() - plans.count(), 0),
            'by_status': by_status,
            'accepted': by_status.get('accepted', 0),
            'cycles': CycleSerializer(GoalCycle.objects.all(), many=True).data,
            'departments': sorted({d for d in people.values_list('department', flat=True) if d}),
        })


class PlanReopenView(GSView):
    """Admin override: put an agreed sheet back with the employee.

    Deliberately writes an event, because reopening an accepted plan is the one
    action that undoes something both sides agreed to.
    """

    def post(self, request, plan_id):
        plan = _plans().filter(id=plan_id).first()
        if not plan:
            return Response({'error': 'Goal sheet not found.'}, status=404)
        note = str(request.data.get('note') or '')
        was = plan.get_status_display()
        plan.status = 'returned'
        plan.save(update_fields=['status'])
        PlanEvent.objects.create(plan=plan, actor_role='admin',
                                 actor_name=str(request.data.get('actor_name') or 'Admin'),
                                 action='reopened',
                                 note=f'Reopened from "{was}". {note}'.strip())
        return Response(PlanSerializer(_plans().get(id=plan.id)).data)


class PlanStatusView(GSView):
    """Admin override: put a sheet at any stage."""

    def post(self, request, plan_id):
        plan = _plans().filter(id=plan_id).first()
        if not plan:
            return Response({'error': 'Goal sheet not found.'}, status=404)
        try:
            force_status(plan, str(request.data.get('status') or ''),
                         name=str(request.data.get('actor_name') or 'Administrator'),
                         note=str(request.data.get('note') or ''))
        except WorkflowError as e:
            return Response({'error': e.message}, status=e.status)
        return Response(PlanSerializer(_plans().get(id=plan.id)).data)


class EmployeeCreateView(GSView):
    """Add one person by hand, for the joiner who missed the upload."""

    def post(self, request):
        emp_id = str(request.data.get('employee_id') or '').strip()
        name = str(request.data.get('name') or '').strip()
        if not emp_id or not name:
            return Response({'error': 'An Employee ID and a name are both required.'}, status=400)
        if EmployeeProfile.objects.filter(employee_id__iexact=emp_id).exists():
            return Response({'error': f'{emp_id} is already on the list.'}, status=400)

        emp = EmployeeProfile.objects.create(
            employee_id=emp_id, name=name,
            email=str(request.data.get('email') or '').strip(),
            phone=str(request.data.get('phone') or '').strip(),
            designation=str(request.data.get('designation') or '').strip(),
            department=str(request.data.get('department') or '').strip(),
            zone=str(request.data.get('zone') or '').strip(),
            reporting_manager_id=str(request.data.get('reporting_manager_id') or '').strip(),
            hod_id=str(request.data.get('hod_id') or '').strip(),
            user_type=str(request.data.get('user_type') or 'employee'),
        )
        return Response(EmployeeSerializer(emp).data, status=201)


class ActivityView(GSView):
    """Every step taken across the whole product, newest first.

    The admin's answer to "what has actually been happening?" - a manager
    returning sheets nobody asked them to, an HOD who has not touched theirs in
    a fortnight. Per-plan history answers one case at a time; this is the view
    across all of them.
    """

    def get(self, request):
        events = (PlanEvent.objects
                  .select_related('plan', 'plan__employee', 'plan__cycle')
                  .order_by('-created_at'))
        if request.query_params.get('cycle_id'):
            events = events.filter(plan__cycle_id=request.query_params['cycle_id'])
        if request.query_params.get('role'):
            events = events.filter(actor_role=request.query_params['role'])

        try:
            limit = min(int(request.query_params.get('limit', 100)), 500)
        except ValueError:
            limit = 100

        return Response([{
            'id': e.id,
            'plan_id': e.plan_id,
            'employee_name': e.plan.employee.name,
            'employee_code': e.plan.employee.employee_id,
            'cycle_name': e.plan.cycle.name,
            'actor_role': e.actor_role,
            'actor_name': e.actor_name,
            'action': e.action,
            'note': e.note,
            'created_at': e.created_at,
        } for e in events[:limit]])


class ResetView(GSView):
    """Clear the product's data. Guarded, and scoped.

    Appraisal Hub has the same feature as one all-or-nothing button. That is
    usually not what someone wants: the common case after a trial run is
    "throw away the goal sheets but keep the people I just uploaded", and an
    all-or-nothing wipe makes them re-upload the master to get back to work.

    So it takes a scope. Both require the confirmation phrase, because this
    deletes the version history too - the one thing the product promises is
    permanent.
    """

    SCOPES = {
        'plans': 'Goal sheets, their versions and their history',
        'people': 'The employee list',
        'all': 'Everything - sheets, history, people and cycles',
    }

    def get(self, request):
        """What a reset would remove, so the count is seen before the click."""
        return Response({
            'scopes': [{'key': k, 'label': v} for k, v in self.SCOPES.items()],
            'counts': {
                'plans': GoalPlan.objects.count(),
                'versions': PlanVersion.objects.count(),
                'events': PlanEvent.objects.count(),
                'people': EmployeeProfile.objects.exclude(employee_id='GS-ADMIN').count(),
                'cycles': GoalCycle.objects.count(),
            },
        })

    def post(self, request):
        if request.data.get('confirm') != 'RESET_CONFIRMED':
            return Response({'error': 'This needs confirmation. Type the phrase to proceed.'},
                            status=400)

        scope = str(request.data.get('scope') or '')
        if scope not in self.SCOPES:
            return Response({'error': f'Choose what to clear: {", ".join(self.SCOPES)}.'},
                            status=400)

        removed = {}
        with transaction.atomic():
            if scope in ('plans', 'all'):
                removed['versions'] = PlanVersion.objects.count()
                removed['events'] = PlanEvent.objects.count()
                removed['plans'] = GoalPlan.objects.count()
                # KRAs and KPIs cascade from the plan.
                GoalPlan.objects.all().delete()

            if scope in ('people', 'all'):
                # The bootstrap admin survives, or nobody could sign back in to
                # undo this - which would be a very poor end to a reset.
                people = EmployeeProfile.objects.exclude(employee_id='GS-ADMIN')
                removed['people'] = people.count()
                people.delete()

            if scope == 'all':
                removed['cycles'] = GoalCycle.objects.count()
                GoalCycle.objects.all().delete()

        return Response({
            'message': f'Cleared: {self.SCOPES[scope].lower()}.',
            'removed': removed,
        })
