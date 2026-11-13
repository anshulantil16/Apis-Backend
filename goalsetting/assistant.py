"""A goal sheet filled in through the Ask APIS chat.

The chat signs in the way Goal Setting does - Employee ID, then the emailed
code, through VerifyOTPView - and saves and submits through PlanView and
PlanActionView, so every rule the portal applies (whose turn it is, a locked
cycle, the KPI fields that must be filled, weightage adding up to exactly
100%) is applied here by the same code, with the same wording.

What this file adds:

  * the session is cut to 30 minutes, not the portal's 12 hours;
  * the token travels in the body, not a header - a custom header is refused
    by the browser's cross-origin check on a developer's machine;
  * the sheet is always the signed-in person's own. The employee id is taken
    from the session, never from the request, so a chat cannot open, save or
    submit anybody else's goals - not even a manager's team member's.

  POST assistant/verify/  {employee_id, otp}                 -> {token, name, sheets, ...}
  POST assistant/save/    {token, cycle_id, kras, submit}    -> the portal's answer
  POST assistant/logout/  {token}                            -> ends the session now

The code itself is sent by the portal's own auth/send-otp/.
"""
from types import SimpleNamespace

from django.core.cache import cache
from rest_framework.response import Response

from .models import CATEGORIES, FREQUENCY_OPTIONS, EmployeeProfile, GoalCycle, GoalPlan
from .services import get_or_create_plan
from .session import _PREFIX, SESSION_HEADER
from .views import GSView, PlanActionView, PlanView, VerifyOTPView

CHAT_SESSION_TTL = 30 * 60
# The portal's own code check does not count wrong guesses. Six digits can be
# guessed given enough goes, so the chat does not give them.
MAX_TRIES = 5
LOCK_SECONDS = 10 * 60
EDITABLE = ('draft', 'returned')
KRA_FIELDS = ('category', 'title', 'description')
KPI_FIELDS = ('metric', 'weightage', 'frequency', 'unit_of_measurement',
              'parameter_type', 'data_source', 'target_value')


def _employee(token):
    token = str(token or '').strip()
    emp_id = cache.get(_PREFIX + token) if token else ''
    if not emp_id:
        return None
    return EmployeeProfile.objects.filter(employee_id__iexact=emp_id, is_active=True).first()


def _as_request(data, token):
    """What Goal Setting's views read off a request, and nothing else."""
    return SimpleNamespace(data=data, query_params={},
                           headers={SESSION_HEADER: token} if token else {})


def _clean(kras):
    """The sheet's own fields only - whatever else the body carries is dropped."""
    out = []
    for k in kras if isinstance(kras, list) else []:
        if not isinstance(k, dict):
            continue
        out.append({**{f: str(k.get(f) or '').strip() for f in KRA_FIELDS},
                    'kpis': [{f: str(p.get(f) or '').strip() for f in KPI_FIELDS}
                             for p in (k.get('kpis') or []) if isinstance(p, dict)]})
    return out


def _sheets(emp):
    """The person's sheet in every open cycle, with whether the chat may fill it."""
    plans = {p.cycle_id: p for p in GoalPlan.objects.filter(employee=emp)
             .prefetch_related('kras__kpis')}
    out = []
    for c in GoalCycle.objects.filter(status='open').order_by('-id'):
        p = plans.get(c.id)
        status = p.status if p else 'draft'
        out.append({
            'cycle_id': c.id, 'cycle_name': c.name, 'fiscal_year': c.fiscal_year,
            'deadline': str(c.submission_deadline) if c.submission_deadline else None,
            'status': status,
            'status_label': p.get_status_display() if p else 'Not started',
            'editable': status in EDITABLE,
            'kras': [{'category': k.category, 'title': k.title, 'description': k.description,
                      'kpis': [{'metric': m.metric, 'weightage': f'{m.weightage:g}',
                                'frequency': m.frequency, 'unit_of_measurement': m.unit_of_measurement,
                                'parameter_type': m.parameter_type, 'data_source': m.data_source,
                                'target_value': m.target_value} for m in k.kpis.all()]}
                     for k in (p.kras.all() if p else [])],
        })
    return out


class AssistantVerifyView(GSView):
    def post(self, request):
        emp_id = str(request.data.get('employee_id') or '').strip()
        tries_key = f'goalsetting-chat-tries:{emp_id.lower()}'
        tries = cache.get(tries_key, 0)
        if tries >= MAX_TRIES:
            return Response({'error': 'Too many wrong codes. Please wait 10 minutes and ask for a new one.'},
                            status=429)
        res = VerifyOTPView().post(_as_request({
            'employee_id': emp_id, 'otp': str(request.data.get('otp') or '').strip(),
        }, ''))
        if res.status_code != 200:
            cache.set(tries_key, tries + 1, timeout=LOCK_SECONDS)
            left = MAX_TRIES - tries - 1
            if left <= 0:
                return Response({'error': 'Too many wrong codes. Please wait 10 minutes and ask for a new one.'},
                                status=429)
            return Response({'error': f"{res.data.get('error', 'That did not work.')} "
                                      f"{left} tr{'y' if left == 1 else 'ies'} left."},
                            status=res.status_code)
        cache.delete(tries_key)
        token = res.data['session']
        emp_id = cache.get(_PREFIX + token)
        cache.set(_PREFIX + token, emp_id, timeout=CHAT_SESSION_TTL)
        emp = EmployeeProfile.objects.get(employee_id=emp_id)
        return Response({
            'token': token, 'expires_in': CHAT_SESSION_TTL,
            'name': emp.name, 'employee_id': emp.employee_id,
            'categories': CATEGORIES, 'frequencies': FREQUENCY_OPTIONS,
            'sheets': _sheets(emp),
        })


class AssistantSaveView(GSView):
    def post(self, request):
        token = str(request.data.get('token') or '').strip()
        emp = _employee(token)
        if not emp:
            return Response({'error': 'Your Goal Setting sign-in has expired. Please sign in again.'},
                            status=401)
        cycle = GoalCycle.objects.filter(id=request.data.get('cycle_id')).first()
        if not cycle:
            return Response({'error': 'That goal-setting cycle was not found.'}, status=404)
        kras = _clean(request.data.get('kras'))
        note = str(request.data.get('note') or '').strip()[:1000]

        if request.data.get('submit'):
            # Save and send on in one step, as the portal's Submit does - a
            # refused submit rolls the save back with it.
            plan = get_or_create_plan(emp, cycle)
            return PlanActionView().post(
                _as_request({'action': 'submit', 'kras': kras, 'note': note}, token), plan.id)
        return PlanView().post(_as_request({'kras': kras}, token), emp.employee_id, cycle.id)


class AssistantLogoutView(GSView):
    def post(self, request):
        token = str(request.data.get('token') or '').strip()
        if token:
            cache.delete(_PREFIX + token)
        return Response({'success': True})
