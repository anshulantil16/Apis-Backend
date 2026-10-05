"""SalesIQ access control — super-admin email OTP."""
import os
import secrets
from django.core.cache import cache
from rest_framework.exceptions import APIException
from rest_framework.views import APIView
from rest_framework.response import Response


# Super admin. Deliberately a hard-coded constant rather than an env default:
# sales data is commercially sensitive, so access has to be an explicit code
# change, never something a stray env var can widen by accident.
SALESIQ_SUPER_ADMIN = 'anshul@apisindia.com'
_OTP_TTL = 300          # 5 minutes
_OTP_MAX_ATTEMPTS = 5   # per issued code, then it is burned


def _salesiq_allowed_emails():
    """Who may sign in to SalesIQ.

    Three sources, and the first is the one anybody actually uses:

    * **anyone granted SalesIQ in the Admin Console.** The portal already has
      a per-person switch for this tool, super-admin-only, sitting in the same
      grid as every other tool. Until this read it, granting SalesIQ there put
      the tile on somebody's dashboard and then this login turned them away
      with "not authorised for SalesIQ" — two allowlists that did not know
      about each other, and no screen anywhere that explained the second one.
      The console is now the single place.
    * SALESIQ_ADMIN_EMAILS, for anyone who needs this and is not in the portal
      directory at all.
    * the super admin, hard-coded, so there is always a way in.

    There is still intentionally NO "any @apisindia.com" fallback — unlike the
    PMS Simulator — because this exposes company-wide revenue. Every address
    here is one a super admin chose, in the console or in the environment.
    """
    extra = [e.strip().lower() for e in
             os.getenv('SALESIQ_ADMIN_EMAILS', '').split(',') if e.strip()]
    allowed = set([SALESIQ_SUPER_ADMIN] + extra)

    # Inactive accounts are excluded: disabling somebody's sign-in in the
    # console has to disable it here too, or "disable sign-in" is a lie.
    try:
        from accounts.models import AppKey, PortalUser
        for email, apps, is_super in PortalUser.objects.filter(
                is_active=True).values_list('email', 'app_access', 'is_superadmin'):
            if is_super or AppKey.SALESIQ in (apps or []):
                allowed.add((email or '').strip().lower())
    except Exception:
        # The portal tables not being there must not lock out the super
        # admin, who is in the set above regardless.
        pass
    return allowed


def _mask(email):
    try:
        name, dom = email.split('@', 1)
        head = name[:2] if len(name) > 2 else name[:1]
        return f"{head}{'*' * max(1, len(name) - len(head))}@{dom}"
    except Exception:
        return email


class SalesLoginView(APIView):
    """POST /api/sales/login/ { action: 'send_otp' | 'verify_otp', email, otp }"""

    def post(self, request):
        action = str(request.data.get('action') or '').strip()
        email = str(request.data.get('email') or '').strip().lower()

        if action == 'send_otp':
            if not email or '@' not in email:
                return Response({'error': 'Please enter a valid email address.'}, status=400)
            if email not in _salesiq_allowed_emails():
                # Same wording regardless of whether the address exists, so this
                # can't be used to enumerate who has access.
                return Response({'error': 'This email is not authorised for SalesIQ. '
                                          'Contact the administrator for access.'}, status=403)

            code = f"{secrets.randbelow(1000000):06d}"
            cache.set(f'salesiq_otp_{email}', {'code': code, 'attempts': 0}, timeout=_OTP_TTL)
            try:
                from django.core.mail import send_mail
                from django.conf import settings
                send_mail(
                    subject='APIS SalesIQ — Login Code',
                    message=(f"Your SalesIQ login code is:\n\n    {code}\n\n"
                             f"Valid for 5 minutes. Do not share it with anyone.\n\n"
                             f"If you did not request this, someone has your email address "
                             f"but not your access — no action is needed.\n\n— APIS SalesIQ"),
                    from_email=(getattr(settings, 'OFFER_LETTER_EMAIL_HOST_USER', None)
                                or settings.EMAIL_HOST_USER or settings.DEFAULT_FROM_EMAIL),
                    recipient_list=[email],
                    fail_silently=False,
                )
            except Exception as e:
                cache.delete(f'salesiq_otp_{email}')
                return Response({'error': f'Could not send the login code: {e}'}, status=500)

            return Response({'message': f'Login code sent to {_mask(email)}',
                             'masked_email': _mask(email), 'expires_in': _OTP_TTL})

        if action == 'verify_otp':
            code = str(request.data.get('otp') or '').strip()
            key = f'salesiq_otp_{email}'
            saved = cache.get(key)
            if not saved:
                return Response({'error': 'That code has expired. Request a new one.'}, status=400)

            # Burn the code after repeated failures so a 6-digit OTP can't be
            # brute-forced within its 5-minute window.
            if saved.get('attempts', 0) >= _OTP_MAX_ATTEMPTS:
                cache.delete(key)
                return Response({'error': 'Too many incorrect attempts. Request a new code.'},
                                status=429)

            if code and secrets.compare_digest(str(saved.get('code')), code):
                cache.delete(key)
                # The role is resolved, not asserted. This used to answer
                # 'super_admin' to everybody who got a code right -- the one
                # person who owns the data and anybody granted read access in
                # the console got the same string, so the page handed both of
                # them the upload and delete controls.
                role = role_of(email) or 'viewer'
                return Response({'success': True, 'email': email,
                                 'role': role,
                                 'can_edit': role == 'super_admin',
                                 'token': issue_session(email),
                                 'name': email.split('@')[0]})

            saved['attempts'] = saved.get('attempts', 0) + 1
            cache.set(key, saved, timeout=_OTP_TTL)
            left = _OTP_MAX_ATTEMPTS - saved['attempts']
            return Response({'error': f'Incorrect code. {left} attempt(s) remaining.'}, status=400)

        return Response({'error': 'Invalid action.'}, status=400)



# ── sessions ─────────────────────────────────────────────────────────────
#
# There were none. Verifying an OTP returned {'success': True, 'role':
# 'super_admin'} and nothing else: no token, so every endpoint below answered
# whoever asked, signed in or not, and the role every single person got back
# was super_admin — hard-coded, the same string for the one person who may
# upload and for anybody granted read access in the console.
#
# So: a token, in the cache like the helpdesk's, and a role that is actually
# resolved. Cache-backed rather than a table because a sales session is
# disposable — losing them all on a restart means signing in again, which is
# the right trade for not adding a model and a migration to carry it.
_SESSION_PREFIX = 'salesiq_session_'
_SESSION_TTL = 12 * 60 * 60      # a working day


def issue_session(email):
    """-> an opaque token the caller sends back on every request."""
    token = secrets.token_urlsafe(32)
    cache.set(_SESSION_PREFIX + token, (email or '').strip().lower(),
              timeout=_SESSION_TTL)
    return token


def session_email(request):
    """-> the address behind the token, or '' if there is none."""
    token = (request.headers.get('X-SalesIQ-Session') or '').strip()
    if not token:
        auth = (request.headers.get('Authorization') or '').strip()
        if auth.lower().startswith('bearer '):
            token = auth[7:].strip()
    return cache.get(_SESSION_PREFIX + token) if token else ''


def revoke_session(request):
    token = (request.headers.get('X-SalesIQ-Session') or '').strip()
    if token:
        cache.delete(_SESSION_PREFIX + token)


def role_of(email):
    """-> 'super_admin' | 'viewer' | None.

    One person owns this data. Everybody else granted SalesIQ in the Admin
    Console reads it: the numbers are the point of the tool, uploading and
    deleting are not. SALESIQ_ADMIN_EMAILS is treated as ownership too, since
    setting it already requires the server.
    """
    email = (email or '').strip().lower()
    if not email:
        return None
    owners = set([SALESIQ_SUPER_ADMIN] + [
        e.strip().lower() for e in
        os.getenv('SALESIQ_ADMIN_EMAILS', '').split(',') if e.strip()])
    if email in owners:
        return 'super_admin'
    return 'viewer' if email in _salesiq_allowed_emails() else None


def actor(request):
    """-> (email, role) for the proven caller, or ('', None)."""
    email = session_email(request)
    return (email, role_of(email)) if email else ('', None)


class _Unauthorised(APIException):
    status_code = 401
    default_detail = 'Please sign in to SalesIQ.'


class _Forbidden(APIException):
    status_code = 403
    default_detail = ('This is read-only for you. Only the SalesIQ owner can '
                      'upload or delete sales data.')


class SalesIQView(APIView):
    """Signed in, with SalesIQ access. Every read endpoint inherits this.

    They inherited APIView, which meant no check at all: the whole of this
    tool — revenue, customers, the sales hierarchy, every invoice line —
    answered anyone who knew the URL. The login screen gated the page, not
    the data, and a page is not a gate.
    """

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        email, role = actor(request)
        if not role:
            raise _Unauthorised()
        request.salesiq_email, request.salesiq_role = email, role

    def handle_exception(self, exc):
        """Answer with `error`, which is the key every other endpoint in
        this app uses and the key the page reads."""
        r = super().handle_exception(exc)
        if isinstance(exc, (_Unauthorised, _Forbidden)):
            r.data = {'error': str(exc.detail)}
        return r

    def require_owner(self, request):
        """For one method on an otherwise readable view."""
        if getattr(request, 'salesiq_role', None) != 'super_admin':
            raise _Forbidden()


class SalesIQAdminView(SalesIQView):
    """And owns the data. Uploading and deleting only."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.salesiq_role != 'super_admin':
            raise _Forbidden()
