"""An Employee of the Month nomination filled in through the Ask APIS chat.

The chat signs in the way EOM does - Employee ID, then the emailed code,
checked by EOMVerifyOTPView - and fills in the same nomination the EOM page
does: track, Part A, the five SMART answers, up to two pieces of evidence,
the declaration and the signature.

It does NOT go through EOMNominationView / EOMSubmitNominationView, and that
is deliberate. Those take whose nomination it is from the URL - an employee
id, a nomination number - so they act on whichever one is named. Here the
nomination is always the signed-in person's own: the employee comes from the
session and nothing in the request can change it. The rules those views
apply (an open cycle, a nomination still with its employee) are applied here
too, and so are the page's own rules for submitting (a track, Part A of at
least 150 words, all five SMART answers, the declaration and a signature),
which until now only the browser checked.

The rest is the other chats' shape:
  * a 30-minute session, not EOM's 12 hours, and the 12-hour one the code
    check opens is closed at once - the chat does not need it;
  * the token travels in the body, not a header;
  * five wrong codes lock the employee out for ten minutes.

  POST assistant/verify/  {employee_id, otp}                -> {token, profile, cycles}
  POST assistant/save/    {token, cycle_id, ...form, submit} -> the nomination
  POST assistant/logout/  {token}                            -> ends the session now

The code itself is sent by EOM's own auth/send-otp/.
"""
import re
import secrets
from types import SimpleNamespace

from django.core.cache import cache
from django.utils import timezone
from rest_framework.response import Response

from accounts.models import AppSession

from .models import EOMCycle, EOMEmployee, EOMNomination
from .views.auth_views import EOMVerifyOTPView, _EOMAuthView

CHAT_SESSION_TTL = 30 * 60
_PREFIX = 'eom-chat-session:'
MAX_TRIES = 5
LOCK_SECONDS = 10 * 60

EDITABLE = ('draft', 'hod_rejected')
TRACKS = ('sales', 'non_sales', 'plant')
MIN_PART_A_WORDS = 150
TEXT_FIELDS = (
    'track', 'part_a_achievement',
    'smart_specific', 'smart_measurable', 'smart_achievable', 'smart_relevant', 'smart_timebound',
    'evidence_1_description', 'evidence_1_source', 'evidence_2_description', 'evidence_2_source',
    'signature_name',
)
SMART = (('smart_specific', 'Specific'), ('smart_measurable', 'Measurable'),
         ('smart_achievable', 'Achievable'), ('smart_relevant', 'Relevant'),
         ('smart_timebound', 'Time-Bound'))
_LOCKED = 'Too many wrong codes. Please wait 10 minutes and ask for a new one.'


def _employee(token):
    token = str(token or '').strip()
    emp_id = cache.get(_PREFIX + token) if token else ''
    if not emp_id:
        return None
    return EOMEmployee.objects.filter(employee_id=emp_id, is_active=True).first()


def _form(nom):
    return {f: getattr(nom, f) or '' for f in TEXT_FIELDS} | {'declaration_agreed': nom.declaration_agreed}


def _cycles(emp):
    """Each open cycle, with this person's own nomination in it if they have one."""
    noms = {n.cycle_id: n for n in EOMNomination.objects.filter(employee=emp)}
    out = []
    for c in EOMCycle.objects.filter(status='open').order_by('-year', '-month'):
        n = noms.get(c.id)
        out.append({
            'cycle_id': c.id, 'name': c.name, 'month': c.get_month_display(), 'year': c.year,
            'status': n.status if n else 'draft',
            'status_label': n.get_status_display() if n else 'Not started',
            'editable': (n.status if n else 'draft') in EDITABLE,
            'form': _form(n) if n else None,
        })
    return out


def _problems(nom):
    """Why this nomination cannot be submitted yet - the EOM page's own rules."""
    out = []
    if nom.track not in TRACKS:
        out.append('Choose your track: Sales, HO or Plant.')
    words = len(re.findall(r'\S+', nom.part_a_achievement or ''))
    if words < MIN_PART_A_WORDS:
        out.append(f'Part A needs at least {MIN_PART_A_WORDS} words (it has {words}).')
    out += [f'Answer the SMART "{label}" question.' for f, label in SMART
            if not str(getattr(nom, f) or '').strip()]
    if not nom.declaration_agreed:
        out.append('Agree to the declaration.')
    if not (nom.signature_name or '').strip():
        out.append('Type your full name as your signature.')
    return out


class AssistantVerifyView(_EOMAuthView):
    def post(self, request):
        emp_id = str(request.data.get('employee_id') or '').strip()
        key = f'eom-chat-tries:{emp_id.lower()}'
        tries = cache.get(key, 0)
        if tries >= MAX_TRIES:
            return Response({'error': _LOCKED}, status=429)

        res = EOMVerifyOTPView().post(SimpleNamespace(
            data={'employee_id': emp_id, 'otp': str(request.data.get('otp') or '').strip()},
            META={}))
        if res.status_code != 200:
            cache.set(key, tries + 1, timeout=LOCK_SECONDS)
            left = MAX_TRIES - tries - 1
            if left <= 0:
                return Response({'error': _LOCKED}, status=429)
            return Response({'error': f"{res.data.get('error', 'That did not work.')} "
                                      f"{left} tr{'y' if left == 1 else 'ies'} left."},
                            status=res.status_code)
        cache.delete(key)

        # The code check opened a 12-hour EOM session. The chat keeps its own
        # shorter one, so that one is closed straight away rather than left
        # lying about.
        AppSession.objects.filter(token_hash=AppSession.hash_token(res.data['session_token'])) \
            .update(revoked_at=timezone.now())

        emp = EOMEmployee.objects.get(employee_id=res.data['employee_id'])
        token = secrets.token_urlsafe(32)
        cache.set(_PREFIX + token, emp.employee_id, timeout=CHAT_SESSION_TTL)
        return Response({
            'token': token, 'expires_in': CHAT_SESSION_TTL,
            'profile': {'name': emp.name, 'employee_id': emp.employee_id,
                        'designation': emp.designation, 'department': emp.department, 'zone': emp.zone},
            # Only an employee nominates themself - the same rule the EOM page
            # applies when it decides who may open the form.
            'can_nominate': emp.user_type == 'employee',
            'cycles': _cycles(emp),
        })


class AssistantSaveView(_EOMAuthView):
    def post(self, request):
        emp = _employee(request.data.get('token'))
        if not emp:
            return Response({'error': 'Your EOM sign-in has expired. Please sign in again.'}, status=401)
        if emp.user_type != 'employee':
            return Response({'error': 'Only employees submit their own nomination.'}, status=403)
        cycle = EOMCycle.objects.filter(id=request.data.get('cycle_id')).first()
        if not cycle:
            return Response({'error': 'That nomination month was not found.'}, status=404)
        if cycle.status != 'open':
            return Response({'error': 'This month is closed for nominations.'}, status=403)

        nom, _ = EOMNomination.objects.get_or_create(employee=emp, cycle=cycle)
        if nom.status not in EDITABLE:
            return Response({'error': f'Your nomination is already "{nom.get_status_display()}", '
                                      f'so it can no longer be changed.'}, status=400)

        for f in TEXT_FIELDS:
            if f in request.data:
                setattr(nom, f, str(request.data.get(f) or '').strip())
        if 'declaration_agreed' in request.data:
            nom.declaration_agreed = request.data.get('declaration_agreed') is True

        if request.data.get('submit'):
            problems = _problems(nom)
            if problems:
                nom.save()      # keep what they wrote; it just is not sent yet
                return Response({'error': 'Your nomination is not complete yet - it is saved as a draft.',
                                 'problems': problems}, status=400)
            nom.status = 'submitted'
            nom.submitted_at = timezone.now()
        nom.save()
        return Response({'id': nom.id, 'status': nom.status, 'status_label': nom.get_status_display(),
                         'cycle': cycle.name})


class AssistantLogoutView(_EOMAuthView):
    def post(self, request):
        token = str(request.data.get('token') or '').strip()
        if token:
            cache.delete(_PREFIX + token)
        return Response({'success': True})
