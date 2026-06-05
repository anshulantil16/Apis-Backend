import secrets
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import EOMEmployee, EOMOTPToken
from ..serializers import EOMEmployeeSerializer

ADMIN_BOOTSTRAP_EMAIL = 'anshul@apisindia.com'
_ADMIN_OTP_CACHE_KEY  = 'eom_admin_bootstrap_otp'
_ADMIN_OTP_TTL        = 300  # 5 minutes


def _mask_email(email: str) -> str:
    if not email or '@' not in email:
        return '***@***.***'
    local, domain = email.split('@', 1)
    if len(local) <= 2:
        masked_local = '*' * len(local)
    else:
        masked_local = local[0] + '*' * (len(local) - 2) + local[-1]
    return f"{masked_local}@{domain}"


class EOMSendOTPView(APIView):
    """POST /api/eom/auth/send-otp/"""

    def post(self, request):
        employee_id = request.data.get('employee_id', '').strip()
        if not employee_id:
            return Response({'error': 'Employee ID is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            emp = EOMEmployee.objects.get(employee_id=employee_id, is_active=True)
        except EOMEmployee.DoesNotExist:
            return Response(
                {'error': 'Employee ID not found. Please check or contact HR.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not emp.email:
            return Response(
                {'error': 'No email address on file. Please contact HR.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        EOMOTPToken.objects.filter(employee=emp, is_used=False).delete()
        otp_code = f"{secrets.randbelow(1_000_000):06d}"
        EOMOTPToken.objects.create(
            employee=emp,
            otp_code=otp_code,
            expires_at=timezone.now() + timedelta(minutes=5),
        )

        import threading
        def _send():
            try:
                send_mail(
                    subject='Your APIS EOM Hub Login OTP',
                    message=(
                        f"Hi {emp.name},\n\n"
                        f"Your one-time password for APIS Employee of the Month Hub is:\n\n"
                        f"  {otp_code}\n\n"
                        f"This OTP is valid for 5 minutes. Do not share it with anyone.\n\n"
                        f"— APIS Team"
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[emp.email],
                    fail_silently=True,
                )
            except Exception:
                pass
        threading.Thread(target=_send, daemon=True).start()

        return Response({
            'message': 'OTP sent successfully.',
            'masked_email': _mask_email(emp.email),
            'name': emp.name,
        })


class EOMVerifyOTPView(APIView):
    """POST /api/eom/auth/verify-otp/"""

    def post(self, request):
        employee_id = request.data.get('employee_id', '').strip()
        otp_code    = request.data.get('otp', '').strip()

        if not employee_id or not otp_code:
            return Response({'error': 'Employee ID and OTP are required.'}, status=400)

        try:
            emp = EOMEmployee.objects.get(employee_id=employee_id, is_active=True)
        except EOMEmployee.DoesNotExist:
            return Response({'error': 'Employee not found.'}, status=404)

        try:
            token = EOMOTPToken.objects.filter(
                employee=emp, is_used=False, otp_code=otp_code,
            ).latest('created_at')
        except EOMOTPToken.DoesNotExist:
            return Response({'error': 'Invalid OTP. Please check and try again.'}, status=400)

        if not token.is_valid():
            return Response({'error': 'OTP has expired. Please request a new one.'}, status=400)

        token.is_used = True
        token.save()

        return Response(EOMEmployeeSerializer(emp).data)


class EOMAdminOTPView(APIView):
    """POST /api/eom/auth/admin-otp/"""

    def post(self, request):
        otp_code = f"{secrets.randbelow(1_000_000):06d}"
        cache.set(_ADMIN_OTP_CACHE_KEY, otp_code, timeout=_ADMIN_OTP_TTL)

        try:
            send_mail(
                subject='APIS EOM Hub — Admin Access OTP',
                message=(
                    f"Admin Login OTP for APIS EOM Hub:\n\n"
                    f"  {otp_code}\n\n"
                    f"Valid for 5 minutes. Do not share this with anyone.\n\n"
                    f"— APIS System"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[ADMIN_BOOTSTRAP_EMAIL],
                fail_silently=False,
            )
        except Exception as e:
            cache.delete(_ADMIN_OTP_CACHE_KEY)
            return Response({'error': f'Failed to send email: {str(e)}'}, status=500)

        return Response({
            'message': 'OTP sent to admin email.',
            'masked_email': _mask_email(ADMIN_BOOTSTRAP_EMAIL),
        })


class EOMAdminVerifyView(APIView):
    """POST /api/eom/auth/admin-verify/"""

    def post(self, request):
        otp_code = request.data.get('otp', '').strip()
        if not otp_code:
            return Response({'error': 'OTP is required.'}, status=400)

        stored = cache.get(_ADMIN_OTP_CACHE_KEY)
        if stored is None:
            return Response({'error': 'OTP expired or not requested.'}, status=400)

        if stored != otp_code:
            return Response({'error': 'Invalid OTP. Please check and try again.'}, status=400)

        cache.delete(_ADMIN_OTP_CACHE_KEY)

        return Response({
            'employee_id': 'ADMIN',
            'name':        'System Admin',
            'designation': 'Administrator',
            'email':       ADMIN_BOOTSTRAP_EMAIL,
            'user_type':   'hr',
            'zone':        '',
            'reporting_manager_id': '',
            'is_active':   True,
        })
