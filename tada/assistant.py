"""TA/DA requests filled in through the Ask APIS chat.

The chat asks the same questions the portal's New Request form does and
submits through the very same views, so every rule the portal enforces -
the policy estimate, the advance ceiling, travel-class entitlement, stays,
ticketing details - is enforced here too, by the same code.

What this file adds is who is asking. The portal's own endpoints take the
employee_id the browser sends; the chat does not. It signs in with the TA/DA
OTP, gets back a short-lived signed token, and every submission is made as
the person that token was issued to - an employee_id in the body is thrown
away. So one person's chat can neither read nor file anything for another.

  POST assistant/verify/   {employee_id, otp}   -> {token, user, caps}
  POST assistant/submit/   {token, kind, data}  -> the portal's answer

The code itself is sent by the portal's own auth/send-otp/.
"""
import json
import secrets
from types import SimpleNamespace

from django.core import signing
from django.core.cache import cache
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from . import policy
from .models import TadaOTP, TadaUser
from .views import CreateLocalTravelView, CreateTourSanctionView, serialize_user

SALT = 'tada.assistant.v1'
TOKEN_AGE = 30 * 60            # seconds a chat sign-in lasts
MAX_TRIES = 5                  # wrong codes before the employee is locked out
LOCK_SECONDS = 10 * 60

# What the chat may file. Travelling Expenses need bills uploaded, which is
# the portal's job, so the chat sends people there instead.
SUBMIT = {
    'tour_sanction': CreateTourSanctionView,
    'local_travel': CreateLocalTravelView,
}


def _tries_key(eid):
    return f'tada-chat-tries:{eid.lower()}'


def _user_for(token):
    """The active TA/DA user this token was issued to, or None."""
    try:
        data = signing.loads(token or '', salt=SALT, max_age=TOKEN_AGE)
    except signing.BadSignature:        # includes SignatureExpired
        return None
    return TadaUser.objects.filter(id=data.get('uid'), employee_id=data.get('eid'),
                                   is_active=True).first()


class AssistantVerifyView(APIView):
    # Not a JWT endpoint: the chat proves who it is with the OTP below, and an
    # intranet session token in the header must not be read as anything here.
    authentication_classes = ()
    permission_classes = ()

    def post(self, request):
        eid = (request.data.get('employee_id') or '').strip()
        code = (request.data.get('otp') or '').strip()
        if not eid or not code:
            return Response({'error': 'Employee ID and code are both needed.'}, status=400)

        # Six digits can be guessed given enough goes, so they are not given.
        tries = cache.get(_tries_key(eid), 0)
        if tries >= MAX_TRIES:
            return Response({'error': 'Too many wrong codes. Please wait 10 minutes and ask for a new one.'},
                            status=429)

        u = TadaUser.objects.filter(employee_id=eid, is_active=True).first()
        otp = (TadaOTP.objects.filter(user=u, code=code, is_used=False).order_by('-created_at').first()
               if u else None)
        if not otp or otp.expires_at < timezone.now():
            cache.set(_tries_key(eid), tries + 1, timeout=LOCK_SECONDS)
            left = MAX_TRIES - tries - 1
            msg = 'That code has expired - ask for a new one.' if otp else 'That code is not right.'
            return Response({'error': f'{msg} {left} tr{"y" if left == 1 else "ies"} left.' if left > 0
                             else 'Too many wrong codes. Please wait 10 minutes and ask for a new one.'},
                            status=400)

        otp.is_used = True
        otp.save(update_fields=['is_used'])
        cache.delete(_tries_key(eid))
        token = signing.dumps({'uid': u.id, 'eid': u.employee_id, 'n': secrets.token_hex(8)}, salt=SALT)
        caps = policy.get_caps(u.level)
        return Response({
            'token': token,
            'expires_in': TOKEN_AGE,
            'user': {k: v for k, v in serialize_user(u).items()
                     if k in ('employee_id', 'name', 'level', 'hq_city', 'designation', 'department')},
            'caps': {
                'mode_options': caps['mode_options'],
                'approved_travel_mode': caps['approved_travel_mode'],
                'local_conveyance_daily': caps['local_conveyance_daily'],
            },
        })


class AssistantSubmitView(APIView):
    authentication_classes = ()
    permission_classes = ()

    def post(self, request):
        # In the body, not a header: a custom header is refused by the
        # browser's cross-origin check unless the whole API allows it.
        u = _user_for(request.data.get('token'))
        if not u:
            return Response({'error': 'Your TA/DA sign-in has expired. Please sign in again.'}, status=401)
        view = SUBMIT.get(request.data.get('kind'))
        if not view:
            return Response({'error': 'The chat can raise a Tour Programme or a Local Travel claim.'}, status=400)
        data = dict(request.data.get('data') or {})
        # Whoever the body claims to be, the request is filed as the person
        # who signed in.
        data.pop('payload', None)
        data['employee_id'] = u.employee_id
        if view is CreateLocalTravelView:
            # The portal sends this one as multipart, the form fields as JSON
            # under "payload" - so it is passed the same way.
            data = {'employee_id': u.employee_id, 'payload': json.dumps(data)}
        # The portal's views read only .data and .query_params, so they are
        # handed exactly that and run unchanged.
        return view().post(SimpleNamespace(data=data, query_params={}))
