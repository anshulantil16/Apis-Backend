"""RoomPulse access control — email OTP login with three roles.

Role resolution order: SUPER_ADMIN_EMAIL constant > AdminUser table > default
Employee. Any @apisindia.com address may log in (as at least Employee) —
unlike SalesIQ, this is a company-wide utility, not a revenue-sensitive tool,
so the bar for entry is "you work here," not an explicit allowlist.
"""
import os
import secrets
from django.core.cache import cache
from rest_framework.views import APIView
from rest_framework.response import Response

from ..models import AdminUser

# Hard-coded, not an env default — matches the SalesIQ precedent of keeping
# the super-admin identity a deliberate code change, not a stray env var.
SUPER_ADMIN_EMAIL = 'anshul@apisindia.com'
_OTP_TTL = 300
_OTP_MAX_ATTEMPTS = 5
_COMPANY_DOMAIN = '@apisindia.com'


def resolve_role(email):
    email = (email or '').strip().lower()
    if email == SUPER_ADMIN_EMAIL:
        return 'super_admin'
    if AdminUser.objects.filter(email=email).exists():
        return 'admin'
    if email.endswith(_COMPANY_DOMAIN):
        return 'employee'
    return None


def _mask(email):
    try:
        name, dom = email.split('@', 1)
        head = name[:2] if len(name) > 2 else name[:1]
        return f"{head}{'*' * max(1, len(name) - len(head))}@{dom}"
    except Exception:
        return email


class RoomPulseLoginView(APIView):
    """POST { action: 'send_otp' | 'verify_otp', email, otp }"""

    def post(self, request):
        action = str(request.data.get('action') or '').strip()
        email = str(request.data.get('email') or '').strip().lower()

        if action == 'send_otp':
            if not email or '@' not in email:
                return Response({'error': 'Please enter a valid email address.'}, status=400)
            role = resolve_role(email)
            if not role:
                return Response({'error': 'Please use your @apisindia.com email address.'},
                                status=403)

            code = f"{secrets.randbelow(1000000):06d}"
            cache.set(f'roompulse_otp_{email}', {'code': code, 'attempts': 0}, timeout=_OTP_TTL)
            try:
                from django.core.mail import send_mail
                from django.conf import settings
                send_mail(
                    subject='RoomPulse — Login Code',
                    message=(f"Your RoomPulse login code is:\n\n    {code}\n\n"
                            f"Valid for 5 minutes. Do not share it with anyone.\n\n"
                            f"— APIS RoomPulse"),
                    from_email=(getattr(settings, 'OFFER_LETTER_EMAIL_HOST_USER', None)
                               or settings.EMAIL_HOST_USER or settings.DEFAULT_FROM_EMAIL),
                    recipient_list=[email],
                    fail_silently=False,
                )
            except Exception as e:
                cache.delete(f'roompulse_otp_{email}')
                return Response({'error': f'Could not send the login code: {e}'}, status=500)

            return Response({'message': f'Login code sent to {_mask(email)}',
                             'masked_email': _mask(email), 'expires_in': _OTP_TTL})

        if action == 'verify_otp':
            code = str(request.data.get('otp') or '').strip()
            key = f'roompulse_otp_{email}'
            saved = cache.get(key)
            if not saved:
                return Response({'error': 'That code has expired. Request a new one.'}, status=400)
            if saved.get('attempts', 0) >= _OTP_MAX_ATTEMPTS:
                cache.delete(key)
                return Response({'error': 'Too many incorrect attempts. Request a new code.'},
                                status=429)
            if code and secrets.compare_digest(str(saved.get('code')), code):
                cache.delete(key)
                role = resolve_role(email)
                return Response({'success': True, 'email': email, 'role': role,
                                 'name': email.split('@')[0].replace('.', ' ').title()})
            saved['attempts'] = saved.get('attempts', 0) + 1
            cache.set(key, saved, timeout=_OTP_TTL)
            left = _OTP_MAX_ATTEMPTS - saved['attempts']
            return Response({'error': f'Incorrect code. {left} attempt(s) remaining.'}, status=400)

        return Response({'error': 'Invalid action.'}, status=400)
