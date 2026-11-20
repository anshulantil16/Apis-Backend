"""The Medical Insurance tool: one click from the intranet into the insurer's
Employee Portal, already signed in.

Two endpoints, both about the signed-in employee and nobody else:

  GET  /api/insurance/overview/   what this tool can do, and for whom
  POST /api/insurance/launch/     mint a one-shot URL and hand it back

There is deliberately no endpoint here that takes an employee id or address.
A token is minted for the caller's own session and for nothing else -- one
that minted a token for a supplied address would be a way to read any
colleague's medical records, which is the one thing the shared secret exists
to prevent.
"""
from accounts.auth import SessionRequiredAPIView, require_app
from accounts.models import AppKey
from accounts.moderation import log_activity
from rest_framework.response import Response

from . import sso


def _employee(user):
    """What the page shows about the person it is about.

    All of it is already the portal's own directory data -- this tool holds
    no medical information of its own, and none passes through it.
    """
    return {
        'name':         user.name,
        'employeeCode': user.employee_code,
        'email':        user.email or '',
        'department':   user.department,
        'designation':  user.designation,
        'location':     user.location,
    }


class OverviewView(SessionRequiredAPIView):
    """What the tool can offer this person, before they click anything."""

    def get(self, request):
        user, err = require_app(request, AppKey.INSURANCE)
        if err:
            return err

        ready = sso.configured()
        # Section 3 allows the employee code when there is no address, so
        # somebody with neither cannot be identified to the insurer at all.
        identified = bool(user.email or user.employee_code)

        if not ready:
            reason = ('The insurance portal is not connected yet. '
                      'It needs the shared secret and portal address from the insurer.')
        elif not identified:
            reason = ('Your record has neither an email address nor an employee code, '
                      'so the insurer cannot be told who you are. Please contact P&C.')
        else:
            reason = ''

        return Response({
            'ready':        ready and identified,
            'reason':       reason,
            'portalName':   sso.portal_name(),
            'employee':     _employee(user),
            'destinations': [{'key': k, 'label': v} for k, v in sso.DESTINATIONS.items()],
        })


class LaunchView(SessionRequiredAPIView):
    """Mint a fresh SSO URL for this employee and hand it back.

    POST {"destination": "claim-status"}  ->  {"url": "https://.../sso/login/..."}

    A fresh token every time, never cached and never stored: section 7 asks
    for one per login attempt, and a stored one is a reusable key to somebody's
    medical records sitting in a database for three hours.
    """

    def post(self, request):
        user, err = require_app(request, AppKey.INSURANCE)
        if err:
            return err

        destination = (request.data.get('destination') or '').strip()
        if destination and destination not in sso.DESTINATIONS:
            return Response({'error': 'That is not a page the insurer accepts.'}, status=400)

        if not (user.email or user.employee_code):
            return Response(
                {'error': 'Your record has no email address or employee code, so the '
                          'insurer cannot be told who you are. Please contact P&C.'},
                status=400)

        try:
            url = sso.launch_url(user, destination)
        except sso.NotConfigured:
            return Response(
                {'error': 'The insurance portal is not connected yet. '
                          'Ask IT to finish the setup with the insurer.'},
                status=503)

        # Worth a line in the audit trail: this is the moment somebody is
        # handed a key to a set of medical records. The URL itself is NOT
        # written down -- it carries the token, and section 10 says not to
        # log it. Only that a launch happened, and to which page.
        log_activity(
            user, 'published',
            summary=f'Opened the insurance portal ({sso.DESTINATIONS.get(destination, "home")})',
            object_type='insurance_sso',
            detail={'destination': destination or 'home'},
            request=request,
        )
        return Response({'url': url})
