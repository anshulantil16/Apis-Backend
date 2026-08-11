"""AdminPulse permission helper.

There is no server-side session/token here — like SalesIQ and the PMS
Simulator, the "session" is just a client-remembered email after OTP
verification. That means every privileged endpoint MUST re-resolve the role
from the email on the server side; the client's claimed role is never
trusted, only used for UI decisions.
"""
from rest_framework.response import Response
from .auth import resolve_role


def actor_role(request):
    """Role for the acting user, taken from ?email= (GET) or body 'email'
    (POST/PATCH/DELETE) — whichever the endpoint received.

    NOTE: `X or Y if C else Z` is a classic trap — Python's conditional
    expression binds looser than `or`, so that reads as `(X or Y) if C else Z`,
    not `X or (Y if C else Z)`. Written explicitly here to avoid it.
    """
    body_email = request.data.get('email') if hasattr(request, 'data') else None
    email = request.query_params.get('email') or body_email
    return resolve_role(email), (email or '').strip().lower()


def require_role(request, *allowed):
    """Returns None if the actor's role is in `allowed`, else a 403 Response.
    Usage: `if (err := require_role(request, 'admin', 'super_admin')): return err`
    """
    role, email = actor_role(request)
    if role not in allowed:
        return Response({'error': 'You do not have permission to perform this action.'},
                        status=403)
    return None
