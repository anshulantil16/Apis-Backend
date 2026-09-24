"""Super Admin: manage who has the Admin or IT Support role."""
from rest_framework.views import APIView
from rest_framework.response import Response

from ..models import AdminUser, Employee
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


class AdminRoleView(APIView):
    """Set one person's role from the directory: Employee, Admin or IT Support.

    One call for all three, because from the screen it is one decision. Doing
    it with the roster's own POST and DELETE meant the caller had to know
    whether a row already existed and hold its id, and changing somebody from
    Admin to IT Support was two requests that could half-fail.
    """

    def post(self, request):
        if (err := require_role(request, 'super_admin')):
            return err
        _, acting_email = actor_role(request)

        email = str(request.data.get('email') or '').strip().lower()
        scope = str(request.data.get('scope') or '').strip()
        if not email or '@' not in email:
            return Response({'error': 'A valid email address is required.'}, status=400)
        if email == SUPER_ADMIN_EMAIL:
            return Response({'error': 'The Super Admin cannot be changed here.'}, status=400)
        if scope not in ('admin', 'it_support', 'employee'):
            return Response({'error': 'Choose Employee, Admin or IT Support.'}, status=400)

        person = Employee.objects.filter(email=email).first()
        name = (person.name if person else str(request.data.get('name') or '')).strip()[:200]

        if scope == 'employee':
            removed = AdminUser.objects.filter(email=email).delete()[0]
            Employee.objects.filter(email=email).update(role='employee')
            return Response({'message': (f'{name or email} is an employee again.'
                                         if removed else f'{name or email} had no extra access.'),
                             'scope': 'employee'})

        row, created = AdminUser.objects.get_or_create(
            email=email, defaults={'name': name, 'scope': scope, 'added_by': acting_email})
        if not created and (row.scope != scope or (name and row.name != name)):
            row.scope, row.name = scope, name or row.name
            row.save(update_fields=['scope', 'name'])

        # The directory's own `role` column is a display mirror -- keep it in
        # step so the list does not contradict the change just made on it.
        Employee.objects.filter(email=email).update(role=scope)
        label = 'Admin' if scope == 'admin' else 'IT Support'
        return Response({'message': f'{name or email} now handles {label} requests.',
                         'scope': scope}, status=201 if created else 200)