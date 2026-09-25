"""The list of people a request can be addressed to.

An employee raising a ticket has to choose somebody, so they need to see the
roster -- but the roster proper (/admins/) is a super-admin screen carrying
who added whom and when. This is the same people, reduced to what a dropdown
needs: a name and the address the request will be filed under.
"""
from rest_framework.response import Response
from rest_framework.views import APIView

from ..assignment import DESK_SCOPE, staff_for
from .perms import require_signed_in


class DeskStaffView(APIView):
    """GET ?desk=it|admin -> [{email, name}], for the picker."""

    def get(self, request):
        if (err := require_signed_in(request)):
            return err
        desk = (request.query_params.get('desk') or '').strip().lower()
        if desk not in DESK_SCOPE:
            return Response({'error': 'Say which desk: it or admin.'}, status=400)
        return Response({'results': [
            {'email': a.email, 'name': a.name or a.email.split('@')[0]}
            for a in staff_for(desk)
        ]})
