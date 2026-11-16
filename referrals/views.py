from datetime import date

from rest_framework.response import Response
from rest_framework import status

from accounts.auth import PortalScopedAPIView, SessionRequiredAPIView, optional_user, require_user
from accounts.moderation import ModerationStatus, log_activity

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


class SubmitReferralView(PortalScopedAPIView):
    """POST — create one referral submission from the Vacancies popup's
    Employee Referral Form.

    Stays open to an unsigned caller: the form is reachable from the wall and
    the popup, and refusing an otherwise-complete referral because a session
    expired mid-form loses a real candidate. But when there IS a session the
    submitter is recorded, so HR can tell a referral sent by the person named
    on it from one where someone typed a colleague's name into the referrer
    box.
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

        referral = EmployeeReferral(**fields)
        # Nothing to publish — see the model docstring — so it arrives
        # approved and never sits in the console's pending queue.
        referral.moderation_status = ModerationStatus.APPROVED
        submitter = optional_user(request)
        if submitter:
            referral.attribute_to(submitter)
        referral.save()

        log_activity(submitter, 'created', referral,
                     summary=f'Referral: {referral.moderation_label()}',
                     detail={'signed_in': bool(submitter),
                             'referrer_typed': referral.referrer_name},
                     request=request)

        return Response({'id': referral.id, 'message': 'Referral submitted.'},
                        status=status.HTTP_201_CREATED)


# ── HR review, inside the intranet's Admin Console ──────────────────────────

HR_FIELDS = ['verified_by', 'verification_date', 'hr_remarks', 'status']


def _require_reviewer(request):
    """(user, None) for someone granted referral review, or (None, 401/403).

    A superadmin always qualifies; anyone else needs `can_review_referrals`,
    switched on per person in the Admin Console.
    """
    user, err = require_user(request)
    if err:
        return None, err
    if not user.reviews_referrals:
        return None, Response({'error': 'You do not have access to referrals. '
                                        'Ask an administrator to grant it.'}, status=403)
    return user, None


def _referral_json(r):
    """Everything the form collected, plus the HR section and who really sent it."""
    out = {f.name: getattr(r, f.name) for f in EmployeeReferral._meta.fields
           if f.name in WRITABLE_FIELDS or f.name in HR_FIELDS}
    for k in ('employment_from', 'employment_to', 'declaration_date', 'verification_date'):
        out[k] = out[k].isoformat() if out[k] else ''
    out.update({
        'id': r.id,
        'recommendation_label': r.get_recommendation_display() if r.recommendation else '',
        'created_at': r.created_at.isoformat(),
        'updated_at': r.updated_at.isoformat(),
        'submitted_by': ({'name': r.submitted_by_name, 'email': r.submitted_by_email}
                         if r.submitted_by_email or r.submitted_by_name else None),
    })
    return out


class AdminReferralsView(SessionRequiredAPIView):
    """GET - every referral, newest first. Only for people granted referral
    review (and superadmins): these carry candidates' and referrers' personal
    details."""

    def get(self, request):
        _, err = _require_reviewer(request)
        if err:
            return err
        rows = EmployeeReferral.objects.order_by('-created_at')
        return Response({'referrals': [_referral_json(r) for r in rows],
                         'statuses': [s for s, _ in EmployeeReferral.STATUS_CHOICES]})


class AdminReferralDetailView(SessionRequiredAPIView):
    """PATCH - the "For HR Use Only" section, and nothing else. What the
    employee wrote on the form is never editable from here."""

    def patch(self, request, referral_id):
        user, err = _require_reviewer(request)
        if err:
            return err
        r = EmployeeReferral.objects.filter(pk=referral_id).first()
        if not r:
            return Response({'error': 'That referral no longer exists.'}, status=404)
        data = request.data
        before = {f: str(getattr(r, f) or '') for f in HR_FIELDS}
        if 'status' in data:
            if data['status'] not in dict(EmployeeReferral.STATUS_CHOICES):
                return Response({'error': 'Unknown status.'}, status=400)
            r.status = data['status']
        if 'verified_by' in data:
            r.verified_by = str(data['verified_by'] or '')[:150]
        if 'hr_remarks' in data:
            r.hr_remarks = str(data['hr_remarks'] or '')
        if 'verification_date' in data:
            try:
                r.verification_date = date.fromisoformat(data['verification_date']) if data['verification_date'] else None
            except ValueError:
                return Response({'error': 'Use a date like 2026-11-13.'}, status=400)
        r.save(update_fields=HR_FIELDS + ['updated_at'])
        after = {f: str(getattr(r, f) or '') for f in HR_FIELDS}
        log_activity(user, 'updated', r, summary=f'Referral review: {r.moderation_label()}',
                     detail={k: [before[k], after[k]] for k in HR_FIELDS if before[k] != after[k]},
                     request=request)
        return Response(_referral_json(r))
