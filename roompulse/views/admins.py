"""Super Admin: manage who has the Admin or IT Support role."""
from rest_framework.views import APIView
from rest_framework.response import Response

from ..models import AdminUser
from .perms import require_role, actor_role
from .auth import SUPER_ADMIN_EMAIL


class AdminRosterView(APIView):
    def get(self, request):
        if (err := require_role(request, 'super_admin')):
            return err
        admins = AdminUser.objects.all()
        return Response({'results': [{
            'id': a.id, 'email': a.email, 'name': a.name, 'scope': a.scope,
            'added_by': a.added_by, 'created_at': a.created_at.isoformat(),
        } for a in admins], 'count': admins.count()})

    def post(self, request):
        if (err := require_role(request, 'super_admin')):
            return err
        _, acting_email = actor_role(request)
        email = str(request.data.get('new_admin_email') or '').strip().lower()
        if not email or '@' not in email:
            return Response({'error': 'A valid email address is required.'}, status=400)
        if email == SUPER_ADMIN_EMAIL:
            return Response({'error': 'That address is already the Super Admin.'}, status=400)
        scope = str(request.data.get('scope') or 'admin').strip()
        if scope not in {c[0] for c in AdminUser.SCOPE_CHOICES}:
            scope = 'admin'
        obj, created = AdminUser.objects.get_or_create(
            email=email,
            defaults={'name': str(request.data.get('name') or '').strip(),
                     'scope': scope, 'added_by': acting_email})
        if not created:
            return Response({'error': f'{email} is already {"an admin" if obj.scope == "admin" else "in IT Support"}.'}, status=400)
        return Response({'message': f'{email} added as {obj.get_scope_display()}.', 'id': obj.id}, status=201)

    def delete(self, request):
        if (err := require_role(request, 'super_admin')):
            return err
        admin_id = request.query_params.get('id')
        try:
            obj = AdminUser.objects.get(id=admin_id)
        except (AdminUser.DoesNotExist, TypeError, ValueError):
            return Response({'error': 'Admin not found.'}, status=404)
        email = obj.email
        obj.delete()
        return Response({'message': f'Removed {email} from admins.'})
