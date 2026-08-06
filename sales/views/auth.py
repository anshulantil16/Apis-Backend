"""SalesIQ access control — super-admin email OTP."""
import os
import secrets
from django.core.cache import cache
from rest_framework.views import APIView
from rest_framework.response import Response


# Super admin. Deliberately a hard-coded constant rather than an env default:
# sales data is commercially sensitive, so access has to be an explicit code
# change, never something a stray env var can widen by accident.
SALESIQ_SUPER_ADMIN = 'anshul@apisindia.com'
_OTP_TTL = 300          # 5 minutes
_OTP_MAX_ATTEMPTS = 5   # per issued code, then it is burned


def _salesiq_allowed_emails():
    """Super admin plus anyone explicitly listed in SALESIQ_ADMIN_EMAILS.
    There is intentionally NO "any @apisindia.com" fallback here — unlike the
    PMS Simulator — because this exposes company-wide revenue."""
    extra = [e.strip().lower() for e in
             os.getenv('SALESIQ_ADMIN_EMAILS', '').split(',') if e.strip()]
    return set([SALESIQ_SUPER_ADMIN] + extra)


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
                return Response({'success': True, 'email': email,
                                 'role': 'super_admin', 'name': email.split('@')[0]})

            saved['attempts'] = saved.get('attempts', 0) + 1
            cache.set(key, saved, timeout=_OTP_TTL)
            left = _OTP_MAX_ATTEMPTS - saved['attempts']
            return Response({'error': f'Incorrect code. {left} attempt(s) remaining.'}, status=400)

        return Response({'error': 'Invalid action.'}, status=400)

