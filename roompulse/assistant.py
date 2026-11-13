"""IT tickets and Admin requests raised through the Ask APIS chat.

The chat signs in exactly as the Help Desk does - email, then the emailed
code, through RoomPulseLoginView - and raises the ticket through the Help
Desk's own TicketListView (an IT ticket) or ResourceRequestListView (an Admin
request - an item, a repair, housekeeping), so the categories, each desk's
roster, the priority rules and who-raised-it all come from the same code as
the forms.

Two things are different, and both are about keeping it to the person:

  * the session is cut to 30 minutes, not the Help Desk's 12 hours. A chat
    is a few minutes' errand, and a token left behind in a browser tab
    should not be good for the rest of the day;
  * the token travels in the request body, not a header. A custom header is
    refused by the browser's cross-origin check on a developer's machine,
    and an Authorization header is read by the intranet's JWT check first.

Whoever the body names, the ticket is raised as the person the token was
issued to (TicketListView takes identity from the session, never the body).

  POST assistant/verify/   {email, otp}            -> {token, name, role, staff, admin_staff}
  POST assistant/ticket/   {token, subject, ...}   -> the Help Desk's answer
  POST assistant/request/  {token, item_name, ...} -> the Help Desk's answer
  POST assistant/logout/   {token}                 -> ends the session now

The code itself is sent by the Help Desk's own login/ {action: 'send_otp'}.
"""
from types import SimpleNamespace

from django.core.cache import cache
from django.utils.datastructures import MultiValueDict
from rest_framework.response import Response
from rest_framework.views import APIView

from .assignment import staff_for
from .people import name_for, name_map
from .views.auth import _SESSION_PREFIX, RoomPulseLoginView
from .views.resource_requests import ResourceRequestListView
from .views.tickets import TicketListView

CHAT_SESSION_TTL = 30 * 60

# What the chat may set on a ticket - the raise form's fields, nothing more.
# In particular not `origin`: logging work is IT's own screen, not a chat.
FIELDS = ('subject', 'description', 'category', 'priority', 'related_to', 'assigned_to_email')
# ... and the Admin request form's.
REQUEST_FIELDS = ('item_name', 'category', 'quantity', 'urgency', 'needed_by', 'reason',
                  'department', 'assigned_to_email')


def _email_for(token):
    token = str(token or '').strip()
    return (cache.get(_SESSION_PREFIX + token) or '') if token else ''


def _as_request(data, token):
    """What the Help Desk's views read off a request, and nothing else."""
    return SimpleNamespace(data=data, headers={'X-AdminPulse-Session': token} if token else {},
                           query_params={}, FILES=MultiValueDict())


class AssistantVerifyView(APIView):
    # Proven by the code below, not by an intranet JWT.
    authentication_classes = ()
    permission_classes = ()

    def post(self, request):
        email = str(request.data.get('email') or '').strip().lower()
        otp = str(request.data.get('otp') or '').strip()
        res = RoomPulseLoginView().post(_as_request(
            {'action': 'verify_otp', 'email': email, 'otp': otp}, ''))
        if res.status_code != 200:
            return res
        token = res.data['token']
        cache.set(_SESSION_PREFIX + token, email, timeout=CHAT_SESSION_TTL)

        def roster(desk):
            people = list(staff_for(desk))
            names = name_map([p.email for p in people])
            return [{'email': p.email,
                     'name': names.get(p.email.lower()) or p.name or p.email.split('@')[0]}
                    for p in people]

        return Response({
            'token': token,
            'expires_in': CHAT_SESSION_TTL,
            'name': res.data.get('name') or email.split('@')[0],
            'role': res.data.get('role'),
            'staff': roster('it'),
            'admin_staff': roster('admin'),
        })


class AssistantTicketView(APIView):
    authentication_classes = ()
    permission_classes = ()

    def post(self, request):
        token = str(request.data.get('token') or '').strip()
        email = _email_for(token)
        if not email:
            return Response({'error': 'Your Help Desk sign-in has expired. Please sign in again.'},
                            status=401)
        data = {k: str(request.data.get(k) or '').strip() for k in FIELDS}
        # The name on the ticket is the signed-in person's, looked up here -
        # the chat does not get to say who is asking.
        data['requested_by_name'] = name_for(email, email.split('@')[0])
        return TicketListView().post(_as_request(data, token))


class AssistantRequestView(APIView):
    """An Admin request: an item, a repair, housekeeping - the other Help Desk form."""
    authentication_classes = ()
    permission_classes = ()

    def post(self, request):
        token = str(request.data.get('token') or '').strip()
        email = _email_for(token)
        if not email:
            return Response({'error': 'Your Help Desk sign-in has expired. Please sign in again.'},
                            status=401)
        data = {k: str(request.data.get(k) or '').strip() for k in REQUEST_FIELDS}
        data['needed_by'] = data['needed_by'] or None
        # As with a ticket: the name is the signed-in person's, looked up here.
        data['requested_by_name'] = name_for(email, email.split('@')[0])
        return ResourceRequestListView().post(_as_request(data, token))


class AssistantLogoutView(APIView):
    authentication_classes = ()
    permission_classes = ()

    def post(self, request):
        token = str(request.data.get('token') or '').strip()
        if token:
            cache.delete(_SESSION_PREFIX + token)
        return Response({'success': True})
