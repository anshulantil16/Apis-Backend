"""PMS Simulator access control — email OTP login."""
import os
import secrets
from django.core.cache import cache
from rest_framework.views import APIView
from rest_framework.response import Response

from .common import _mask_email

PMS_BOOTSTRAP_EMAIL = 'anshul@apisindia.com'
_PMS_OTP_TTL = 300  # 5 minutes


def _pms_allowed_emails():
    """Explicit allowlist: the bootstrap admin + anyone in the PMS_ADMIN_EMAILS env
    (comma-separated). This is the master access list for the PMS Simulator."""
    extra = [e.strip().lower() for e in os.getenv('PMS_ADMIN_EMAILS', '').split(',') if e.strip()]
    return set([PMS_BOOTSTRAP_EMAIL] + extra)


def _pms_email_authorized(email):
    """Authorized only if the email is on the allowlist (bootstrap admin +
    PMS_ADMIN_EMAILS). Any @apisindia.com address is allowed ONLY when
    PMS_ALLOW_ANY_COMPANY_EMAIL=true (kept on QA for convenience; OFF on PROD so
    access is restricted to the named allowlist)."""
    email = (email or '').strip().lower()
    if email in _pms_allowed_emails():
        return True
    if os.getenv('PMS_ALLOW_ANY_COMPANY_EMAIL', 'true').lower() == 'true' and email.endswith('@apisindia.com'):
        return True
    return False



class PMSLoginView(APIView):
    """POST /api/pms/login/  { action: 'send_otp' | 'verify_otp', email, otp }.

    Sends a 4-digit OTP to the entered email (must be authorized) and verifies it.
    OTP is stored in the shared DatabaseCache so it works across gunicorn workers."""

    def post(self, request):
        action = str(request.data.get('action') or '').strip()
        email = str(request.data.get('email') or '').strip().lower()

        if action == 'send_otp':
            if not email or '@' not in email:
                return Response({'error': 'Please enter a valid email address.'}, status=400)
            if not _pms_email_authorized(email):
                return Response({'error': 'This email is not authorized to access the PMS '
                                          'Simulator. Please contact the administrator.'}, status=403)

            otp_code = f"{secrets.randbelow(10000):04d}"
            cache.set(f'pms_login_otp_{email}', otp_code, timeout=_PMS_OTP_TTL)
            try:
                from django.core.mail import send_mail
                from django.conf import settings
                send_mail(
                    subject='APIS PMS Simulator — Login OTP',
                    message=(f"Your one-time password (OTP) for the APIS PMS Simulator is:\n\n"
                             f"  {otp_code}\n\n"
                             f"This OTP is valid for 5 minutes. Do not share it with anyone.\n\n"
                             f"— APIS System"),
                    from_email=settings.EMAIL_HOST_USER or settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[email],
                    fail_silently=False,
                )
            except Exception as e:
                cache.delete(f'pms_login_otp_{email}')
                return Response({'error': f'Could not send OTP email: {e}'}, status=500)

            return Response({'message': f'OTP sent to {_mask_email(email)}',
                             'masked_email': _mask_email(email)})

        if action == 'verify_otp':
            otp_code = str(request.data.get('otp') or '').strip()
            saved = cache.get(f'pms_login_otp_{email}')
            if saved and otp_code and saved == otp_code:
                cache.delete(f'pms_login_otp_{email}')
                return Response({'success': True, 'email': email})
            return Response({'error': 'Invalid or expired OTP. Please request a new one.'}, status=400)

        return Response({'error': 'Invalid action.'}, status=400)

