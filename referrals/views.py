from datetime import date

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import EmployeeReferral

REQUIRED_FIELDS = ['candidate_name', 'position_applied_for', 'referrer_name', 'recommendation']

# Fields the form is allowed to set. HR-only review fields (verified_by,
# verification_date, hr_remarks, status) are deliberately excluded here —
# a submission always starts life as 'Pending' and gets reviewed from the
# Django admin, not from this endpoint.
WRITABLE_FIELDS = [
    'candidate_name', 'position_applied_for', 'candidate_department', 'candidate_employee_id',
    'candidate_contact', 'candidate_email',
    'referrer_name', 'referrer_employee_id', 'referrer_designation', 'referrer_department',
    'referrer_contact', 'referrer_email',
    'relationship', 'relationship_other', 'association_duration',
    'previous_organization', 'candidate_previous_designation', 'employment_from', 'employment_to',
    'reporting_to',
    'technical_skills', 'strengths', 'areas_of_improvement', 'overall_performance',
    'recommendation', 'disciplinary_issues', 'disciplinary_details',
    'referrer_signature', 'declaration_date',
]


def _parse_date(value):
    """Empty strings are valid "not provided" for an optional DateField, but
    Django's own date parsing rejects them outright rather than treating
    them as null — so blank has to be normalised before the field ever
    sees it."""
    return value or None


class SubmitReferralView(APIView):
    """POST — create one referral submission from the Vacancies popup's
    Employee Referral Form. No portal session is required to submit (same
    "trust the client" posture as tada/sales/roompulse elsewhere in this
    project — see roompulse/views/perms.py for the fuller rationale);
    review happens afterwards from the Django admin.
    """

    def post(self, request):
        data = request.data
        missing = [f for f in REQUIRED_FIELDS if not str(data.get(f, '')).strip()]
        if missing:
            return Response({'error': f'Missing required field(s): {", ".join(missing)}'},
                             status=status.HTTP_400_BAD_REQUEST)

        fields = {k: data.get(k, '') for k in WRITABLE_FIELDS if k in data}
        fields['employment_from'] = _parse_date(fields.get('employment_from'))
        fields['employment_to'] = _parse_date(fields.get('employment_to'))
        fields['declaration_date'] = _parse_date(fields.get('declaration_date')) or date.today()
        fields['disciplinary_issues'] = str(fields.get('disciplinary_issues', '')).lower() in ('true', 'yes', '1')

        referral = EmployeeReferral.objects.create(**fields)
        return Response({'id': referral.id, 'message': 'Referral submitted.'}, status=status.HTTP_201_CREATED)
