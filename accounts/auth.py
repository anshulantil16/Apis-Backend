"""Who is making this request, and are they allowed to?

Lives apart from views.py so that any app can ask the question. Before this,
`current_session` sat inside accounts.views, and a second app wanting to know
its caller had to import a views module to do it — which is how tools ended up
with no identity at all and the "trust the client" posture spread through
vacancies, referrals, tada and sales.

Nothing here decides *what* a user may do beyond the superadmin flag; per-tool
access still comes from PortalUser.can_open.
"""
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import PortalSession


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
