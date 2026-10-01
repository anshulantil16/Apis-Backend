"""The list of people a request can be addressed to.

An employee raising a ticket has to choose somebody, so they need to see the
roster -- but the roster proper (/admins/) is a super-admin screen carrying
who added whom and when. This is the same people, reduced to what a dropdown
needs: a name and the address the request will be filed under.
"""
from rest_framework.response import Response
from rest_framework.views import APIView

from ..assignment import DESK_SCOPE, staff_for
from ..people import name_for, name_map
from .perms import actor_role, require_signed_in


class DeskStaffView(APIView):
    """GET ?desk=it|admin -> [{email, name}], for the picker."""

    def get(self, request):
        if (err := require_signed_in(request)):
            return err
        desk = (request.query_params.get('desk') or '').strip().lower()
        if desk not in DESK_SCOPE:
            return Response({'error': 'Say which desk: it or admin.'}, status=400)
        people = list(staff_for(desk))
        # The roster carries whatever the super admin typed; HRMS carries
        # what the person is actually called. The dropdown is a list of
        # colleagues, so it should read like one.
        proper = name_map([a.email for a in people])
        return Response({'results': [
            {'email': a.email,
             'name': proper.get(a.email.lower()) or a.name or a.email.split('@')[0]}
            for a in people
        ]})


class WhoAmIView(APIView):
    """GET -> who the session belongs to, freshly resolved.

    The name is saved into the browser when somebody signs in and read back
    from there on every later visit, so a session minted before the name was
    being resolved properly keeps the old one until that person happens to
    sign out -- which could be weeks. This lets the page correct itself on
    load instead, and also picks up a name changed in HRMS since.
    """

    def get(self, request):
        if (err := require_signed_in(request)):
            return err
        role, email = actor_role(request)
        return Response({'email': email, 'role': role, 'name': name_for(email)})
