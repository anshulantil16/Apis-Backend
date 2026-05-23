import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import EmployeeProfile, OTPToken
from ..serializers import EmployeeProfileSerializer


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