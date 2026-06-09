import secrets
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import EmployeeProfile, OTPToken
from ..serializers import EmployeeProfileSerializer

# Hardcoded admin bootstrap email — OTP for first-time/admin login always goes here
ADMIN_BOOTSTRAP_EMAIL = 'anshul@apisindia.com'
_ADMIN_OTP_CACHE_KEY = 'admin_bootstrap_otp'
_ADMIN_OTP_TTL = 300  # 5 minutes


def _mask_email(email: str) -> str:
    if not email or '@' not in email:
        return '***@***.***'
    local, domain = email.split('@', 1)
    if len(local) <= 2:
        masked_local = '*' * len(local)
    else:
        masked_local = local[0] + '*' * (len(local) - 2) + local[-1]
    return f"{masked_local}@{domain}"


class SendOTPView(APIView):
    """POST /api/performance/auth/send-otp/"""

    def post(self, request):
        employee_id = request.data.get('employee_id', '').strip()
        if not employee_id:
            return Response({'error': 'Employee ID is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            emp = EmployeeProfile.objects.get(employee_id=employee_id, is_active=True)
        except EmployeeProfile.DoesNotExist:
            return Response(
                {'error': 'Employee ID not found. Please check and try again.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except EmployeeProfile.MultipleObjectsReturned:
            # Same employee_id exists in both hubs — pick by app_source
            source = getattr(request, 'app_source', 'performance')
            try:
                emp = EmployeeProfile.objects.get(employee_id=employee_id, is_active=True, app_source=source)
            except EmployeeProfile.DoesNotExist:
                emp = EmployeeProfile.objects.filter(employee_id=employee_id, is_active=True).first()

        if not emp.email:
            return Response(
                {'error': 'No email address on file for this ID. Please contact HR to update your profile.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Invalidate any existing unused OTPs for this employee
        OTPToken.objects.filter(employee=emp, is_used=False).delete()

        otp_code = f"{secrets.randbelow(1_000_000):06d}"
        OTPToken.objects.create(
            employee=emp,
            otp_code=otp_code,
            expires_at=timezone.now() + timedelta(minutes=5),
        )

        send_mail(
            subject='Your APIS Performance Hub Login OTP',
            message=(
                f"Hi {emp.name},\n\n"
                f"Your one-time password (OTP) for APIS Performance Hub is:\n\n"
                f"  {otp_code}\n\n"
                f"This OTP is valid for 5 minutes. Do not share it with anyone.\n\n"
                f"— APIS Team"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[emp.email],
            fail_silently=False,
        )

        return Response({
            'message': 'OTP sent successfully.',
            'masked_email': _mask_email(emp.email),
            'name': emp.name,
        })


class VerifyOTPView(APIView):
    """POST /api/performance/auth/verify-otp/"""

    def post(self, request):
        employee_id = request.data.get('employee_id', '').strip()
        otp_code = request.data.get('otp', '').strip()

        if not employee_id or not otp_code:
            return Response(
                {'error': 'Employee ID and OTP are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            emp = EmployeeProfile.objects.get(employee_id=employee_id, is_active=True)
        except EmployeeProfile.DoesNotExist:
            return Response({'error': 'Employee not found.'}, status=status.HTTP_404_NOT_FOUND)
        except EmployeeProfile.MultipleObjectsReturned:
            source = getattr(request, 'app_source', 'performance')
            try:
                emp = EmployeeProfile.objects.get(employee_id=employee_id, is_active=True, app_source=source)
            except EmployeeProfile.DoesNotExist:
                emp = EmployeeProfile.objects.filter(employee_id=employee_id, is_active=True).first()

        try:
            token = OTPToken.objects.filter(
                employee=emp,
                is_used=False,
                otp_code=otp_code,
            ).latest('created_at')
        except OTPToken.DoesNotExist:
            return Response(
                {'error': 'Invalid OTP. Please check and try again.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not token.is_valid():
            return Response(
                {'error': 'OTP has expired. Please request a new one.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        token.is_used = True
        token.save()

        return Response(EmployeeProfileSerializer(emp).data)


class AdminBootstrapOTPView(APIView):
    """POST /api/performance/auth/admin-otp/
    Sends an OTP to the hardcoded admin email. Works even before any employees are imported.
    """

    def post(self, request):
        otp_code = f"{secrets.randbelow(1_000_000):06d}"
        cache.set(_ADMIN_OTP_CACHE_KEY, otp_code, timeout=_ADMIN_OTP_TTL)

        try:
            send_mail(
                subject='APIS Appraisal Hub — Admin Access OTP',
                message=(
                    f"Admin Login OTP for APIS Appraisal Hub:\n\n"
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
            return Response(
                {'error': f'Failed to send email: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response({
            'message': 'OTP sent to admin email.',
            'masked_email': _mask_email(ADMIN_BOOTSTRAP_EMAIL),
        })


class AdminBootstrapVerifyView(APIView):
    """POST /api/performance/auth/admin-verify/
    Verifies the admin bootstrap OTP and returns a synthetic admin profile.
    """

    def post(self, request):
        otp_code = request.data.get('otp', '').strip()
        if not otp_code:
            return Response({'error': 'OTP is required.'}, status=status.HTTP_400_BAD_REQUEST)

        stored = cache.get(_ADMIN_OTP_CACHE_KEY)
        if stored is None:
            return Response(
                {'error': 'OTP expired or not requested. Please send a new one.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if stored != otp_code:
            return Response(
                {'error': 'Invalid OTP. Please check and try again.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cache.delete(_ADMIN_OTP_CACHE_KEY)

        return Response({
            'employee_id': 'ADMIN',
            'name': 'System Admin',
            'designation': 'Administrator',
            'email': ADMIN_BOOTSTRAP_EMAIL,
            'user_type': 'hr',
            'zone': '',
            'reporting_manager_id': '',
            'is_active': True,
        })