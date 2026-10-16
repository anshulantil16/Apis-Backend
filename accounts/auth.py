"""Who is making this request, and are they allowed to?

Lives apart from views.py so that any app can ask the question. Before this,
`current_session` sat inside accounts.views, and a second app wanting to know
its caller had to import a views module to do it — which is how tools ended up
with no identity at all and the "trust the client" posture spread through
vacancies, referrals, tada and sales.

Nothing here decides *what* a user may do beyond the superadmin flag; per-tool
access still comes from PortalUser.can_open.
"""
from django.conf import settings

from rest_framework import status
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AppSession, PortalSession


def client_ip(request):
    """Best guess at the caller's address, for the activity log.

    Behind nginx the socket address is always the proxy, so the forwarded
    header is preferred — first entry, which is the original client.
    """
    fwd = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if fwd:
        return fwd.split(',')[0].strip()[:64]
    return (request.META.get('REMOTE_ADDR') or '')[:64]


def current_session(request):
    """The live session behind this request, or None.

    Read from the Authorization header rather than a cookie so the same
    endpoints serve the SPA and any future non-browser caller identically.
    """
    raw = request.META.get('HTTP_AUTHORIZATION', '')
    token = raw[7:].strip() if raw.lower().startswith('bearer ') else raw.strip()
    if not token:
        return None
    s = (PortalSession.objects
         .select_related('user')
         .filter(token_hash=PortalSession.hash_token(token))
         .first())
    return s if (s and s.is_live and s.user.is_active) else None


def require_user(request):
    """(user, None) for a signed-in caller, or (None, 401 response).

    Use for anything that writes. An anonymous write cannot be attributed to
    anyone, which is exactly what the activity log exists to prevent.
    """
    s = current_session(request)
    if not s:
        return None, Response({'error': 'Please sign in to continue.'}, status=401)
    return s.user, None


def require_superadmin(request):
    """(user, None) for a superadmin, or (None, 401/403 response)."""
    user, err = require_user(request)
    if err:
        return None, err
    if not user.is_superadmin:
        return None, Response({'error': 'This action is for administrators.'}, status=403)
    return user, None


def require_app(request, app_key):
    """(user, None) for someone granted a given tool, or (None, 401/403).

    The Admin Console matrix decides who may open what. Until this existed
    the matrix only decided which TILES were drawn -- the API behind a tool
    answered any signed-in employee, so the grant was a courtesy rather than
    a control. Anything holding salaries or personal records should call
    this rather than require_user.
    """
    user, err = require_user(request)
    if err:
        return None, err
    if not user.can_open(app_key):
        return None, Response(
            {'error': 'You do not have access to this tool. Ask an administrator to grant it.'},
            status=403)
    return user, None


def require_tree_editor(request):
    """(user, None) for someone allowed to edit APIS Tree cards, or (None, 401/403).

    A superadmin always qualifies; anyone else needs `can_edit_tree` granted
    from Admin Console — Super Admin's way of delegating this one thing
    (name/photo/designation edits on the org chart) without handing out the
    whole console.

    Managing the chart implies editing it: somebody trusted to add a person
    cannot sensibly be barred from correcting their title afterwards.
    """
    user, err = require_user(request)
    if err:
        return None, err
    if not (user.is_superadmin or user.can_edit_tree or user.can_manage_tree):
        return None, Response({'error': 'You are not able to edit APIS Tree.'}, status=403)
    return user, None


def require_tree_manager(request):
    """(user, None) for someone allowed to change WHO is on APIS Tree.

    Adding a person, removing one, or moving somebody under a different HOD
    — as opposed to correcting what an existing card says, which is
    require_tree_editor above.

    Separate because the two are not the same size. A misspelt designation is
    routine and worth delegating widely; taking somebody off the org chart is
    structural and visible to the whole company. Held under one grant, anyone
    who could fix a typo could also delete the managing director's card.
    """
    user, err = require_user(request)
    if err:
        return None, err
    if not (user.is_superadmin or user.can_manage_tree):
        return None, Response(
            {'error': 'You can edit APIS Tree cards, but not add or remove '
                      'people. Ask a Super Admin if you need that.'}, status=403)
    return user, None


def optional_user(request):
    """Whoever is signed in, or None — for endpoints that stay open to all
    but should still attribute the caller when there is one."""
    s = current_session(request)
    return s.user if s else None


class PortalScopedAPIView(APIView):
    """Base for endpoints that authenticate with a portal session token.

    Opts out of DRF's project-wide JWTAuthentication. These views carry
    opaque session tokens in the Authorization header, and SimpleJWT would
    try to parse one as a JWT, fail, and answer 401 before any code here
    ran — so a perfectly good session looked like a rejected one. Same
    reasoning as accounts.views.PortalAPIView; this is the version other
    apps inherit.
    """
    authentication_classes = []
    permission_classes = []


class SessionRequiredAPIView(PortalScopedAPIView):
    """Base for endpoints that must not answer anyone who is not signed in.

    PortalScopedAPIView turns DRF's own authentication off and leaves each
    view to check for itself. That is right for endpoints with a rule of
    their own -- "approved documents to everyone, pending ones to their
    uploader" -- and wrong as a default, because a view that simply forgets
    to check answers the whole world. On 14-10-2026 a sweep of the live site
    with no session at all found 40 endpoints answering 200, among them the
    full PMS employee table: 317 people with four years of CTC, their
    increment amounts and their new CTC, plus the same as a spreadsheet.

    Inheriting this instead moves the default the other way: no valid portal
    session, no answer, and a view has to opt out deliberately to be public.
    It says nothing about WHAT the caller may see -- a view that shows one
    person's data still has to check it is their data. It only settles that
    there is a caller.
    """

    def dispatch(self, request, *args, **kwargs):
        # Answered here rather than by raising NotAuthenticated, which DRF
        # turns into 403 when no authentication class is installed -- and
        # these views install none on purpose (see PortalScopedAPIView).
        # The rest of the portal answers 401 for "not signed in", and the
        # frontend drops a dead token when it sees one; a 403 would leave it
        # retrying with a token that will never work again.
        if current_session(self.initialize_request(request, *args, **kwargs)) is None:
            r = Response({'error': 'Sign in to the intranet to use this.'},
                         status=status.HTTP_401_UNAUTHORIZED)
            r.accepted_renderer = JSONRenderer()
            r.accepted_media_type = 'application/json'
            r.renderer_context = {}
            return r.render()
        return super().dispatch(request, *args, **kwargs)


# ── the standalone tools: Appraisal and EOM ─────────────────────────────────

def _bearer(request):
    raw = request.META.get('HTTP_AUTHORIZATION', '')
    return raw[7:].strip() if raw.lower().startswith('bearer ') else raw.strip()


def current_app_session(request, app):
    """The live AppSession behind this request for `app`, or None."""
    token = _bearer(request)
    if not token:
        return None
    s = (AppSession.objects
         .filter(app=app, token_hash=AppSession.hash_token(token))
         .first())
    return s if (s and s.is_live) else None


def signed_in_for(request, app):
    """Whether this request carries ANY credential good for `app`.

    Either of two counts. A portal session, because inside the intranet
    these tools are opened by someone already signed in and sending that
    token. Or the app's own session, for the people who use Appraisal and
    EOM on their own, outside the portal, with the OTP login those tools
    have always had.
    """
    return current_session(request) is not None or current_app_session(request, app) is not None


class AppSessionRequiredAPIView(PortalScopedAPIView):
    """Base for Appraisal and EOM endpoints. Subclasses set APP.

    Enforcement is behind settings.ENFORCE_APP_SESSIONS, which is off by
    default, and that is the point rather than an oversight. Turning the
    requirement on in the same release that starts issuing the tokens would
    sign out everyone who was already working -- mid goal sheet, mid
    nomination -- because their browser has no token to send yet. So this
    ships inert: the login starts handing out sessions, the pages start
    sending them, and nobody notices. Once the people using these tools are
    carrying tokens, the flag goes on and the door shuts.

    Checked per request rather than at import, so the flag can be turned on
    with a restart and turned off again just as fast if it bites.
    """
    APP = ''

    def dispatch(self, request, *args, **kwargs):
        if getattr(settings, 'ENFORCE_APP_SESSIONS', False):
            drf_request = self.initialize_request(request, *args, **kwargs)
            if not signed_in_for(drf_request, self.APP):
                r = Response({'error': 'Please sign in again to continue.'},
                             status=status.HTTP_401_UNAUTHORIZED)
                r.accepted_renderer = JSONRenderer()
                r.accepted_media_type = 'application/json'
                r.renderer_context = {}
                return r.render()
        return super().dispatch(request, *args, **kwargs)
