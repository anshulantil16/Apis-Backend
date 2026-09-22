"""AdminPulse permission helper.

Identity comes from the session token minted at OTP verification and sent
back in the X-AdminPulse-Session header -- see auth.issue_session().

It used to come from an `email` field in the request itself, which meant the
login was decorative: anyone could act as anyone by typing their address,
including the super admin, whose address is a constant in auth.py. The role
was carefully re-resolved server-side, which looked like a check, but it was
re-resolving a name the caller had just chosen for themselves.

The rule this file now enforces: an email in a request body is data about the
request. It is never a claim about who is making it.
"""
from rest_framework.response import Response
from .auth import resolve_role, session_email


def actor_role(request):
    """-> (role, email) for the proven sender, or (None, '') if unproven.

    Memoised on the request: a view typically asks once to check permission
    and again to find out who it is talking to, and each ask was a cache read
    plus a roster query. Resolving twice per request is not expensive, but it
    is twice as many chances for the two answers to disagree.
    """
    cached = getattr(request, '_adminpulse_actor', None)
    if cached is not None:
        return cached
    email = session_email(request)
    result = (resolve_role(email), email) if email else (None, '')
    try:
        request._adminpulse_actor = result
    except AttributeError:
        pass
    return result


def require_role(request, *allowed):
    """Returns None if the actor's role is in `allowed`, else 401/403.
    Usage: `if (err := require_role(request, 'admin', 'super_admin')): return err`
    """
    role, email = actor_role(request)
    if not email:
        return Response({'error': 'Please sign in again.'}, status=401)
    if role not in allowed:
        return Response({'error': 'You do not have permission to perform this action.'},
                        status=403)
    return None


def require_signed_in(request):
    """Any verified employee. -> None, or a 401 Response."""
    role, email = actor_role(request)
    if not email or not role:
        return Response({'error': 'Please sign in again.'}, status=401)
    return None
