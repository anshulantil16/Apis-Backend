"""Super Admin: employee directory — template, bulk upload, list, clear."""
import io
import openpyxl
from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser

from ..models import Employee, AdminUser
from ..ingest import map_headers, build_template, role_grant
from .perms import require_role, actor_role
from .auth import SUPER_ADMIN_EMAIL


class EmployeeTemplateView(APIView):
    def get(self, request):
        buf = build_template()
        resp = HttpResponse(
            buf.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = 'attachment; filename="AdminPulse_Employee_Template.xlsx"'
        return resp


class EmployeeUploadView(APIView):
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        if (err := require_role(request, 'super_admin')):
            return err
        _, acting_email = actor_role(request)
        f = request.FILES.get('file')
        if not f:
            return Response({'error': 'No file provided.'}, status=400)
        try:
            wb = openpyxl.load_workbook(f, data_only=True)
            ws = wb.active
        except Exception as e:
            return Response({'error': f'Cannot read file: {e}'}, status=400)

        try:
            header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
        except StopIteration:
            return Response({'error': 'The sheet is empty.'}, status=400)

        col_map, unknown = map_headers(header_row)
        raw_headers = [str(c).strip() for c in header_row if c is not None and str(c).strip()]
        if 'name' not in col_map or 'email' not in col_map:
            missing = [lbl for field, lbl in (('name', 'Name'), ('email', 'Email')) if field not in col_map]
            shown = ', '.join(raw_headers[:15]) + (' …' if len(raw_headers) > 15 else '')
            return Response({
                'error': f'Missing required column(s): {", ".join(missing)}. '
                         f'Your file has: {shown or "(no headers found)"}.',
                'detected_columns': raw_headers,
            }, status=400)

        def cell(row, field):
            ci = col_map.get(field)
            if ci is None or ci >= len(row):
                return None
            v = row[ci]
            return str(v).strip() if v is not None else ''

        created = updated = skipped = admins_granted = it_support_granted = 0
        skipped_rows = []
        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not any(v is not None and str(v).strip() != '' for v in row):
                continue
            email = (cell(row, 'email') or '').lower()
            name = cell(row, 'name') or ''
            if not email or '@' not in email or not name:
                skipped += 1
                skipped_rows.append(row_idx)
                continue

            # Role column GRANTS admin/IT-Support access, never revokes it —
            # a blank or "Employee" cell on someone who already has access
            # leaves them untouched. Revoking access stays a deliberate
            # action in the Team tab.
            grant = role_grant(cell(row, 'role')) if email != SUPER_ADMIN_EMAIL else None

            obj, was_created = Employee.objects.update_or_create(
                email=email,
                defaults={
                    'name': name[:200],
                    'employee_code': (cell(row, 'employee_code') or '')[:50],
                    'department': (cell(row, 'department') or '')[:150],
                    'designation': (cell(row, 'designation') or '')[:150],
                    'location': (cell(row, 'location') or '')[:150],
                    'reporting_manager': (cell(row, 'reporting_manager') or '')[:200],
                    **({'role': grant} if grant else {}),
                },
            )
            created += was_created
            updated += not was_created

            if grant:
                _, was_new_admin = AdminUser.objects.get_or_create(
                    email=email, defaults={'name': name[:200], 'scope': grant,
                                           'added_by': f'employee upload by {acting_email}'})
                if grant == 'admin':
                    admins_granted += was_new_admin
                else:
                    it_support_granted += was_new_admin

        warnings = []
        if skipped_rows:
            head = ', '.join(str(n) for n in skipped_rows[:15])
            warnings.append(f'{skipped} row(s) skipped — missing Name or a valid Email '
                            f'(sheet row {head}{"…" if len(skipped_rows) > 15 else ""}).')
        if unknown:
            warnings.append(f'{len(unknown)} column(s) not recognised: {", ".join(unknown[:10])}.')
        if admins_granted:
            warnings.append(f'{admins_granted} employee(s) granted Admin access via the Role column.')
        if it_support_granted:
            warnings.append(f'{it_support_granted} employee(s) granted IT Support access via the Role column.')

        return Response({
            'message': f'{created} added, {updated} updated.'
                       + (f' {admins_granted} granted Admin access.' if admins_granted else '')
                       + (f' {it_support_granted} granted IT Support access.' if it_support_granted else ''),
            'created': created, 'updated': updated, 'skipped': skipped,
            'admins_granted': admins_granted, 'it_support_granted': it_support_granted,
            'detected_columns': raw_headers, 'warnings': warnings,
        })


class EmployeeListView(APIView):
    def get(self, request):
        # The whole staff list: every name, email address and department.
        # This was readable by anyone signed in, which is most of the company.
        if (err := require_role(request, 'admin', 'it_support', 'super_admin')):
            return err
        from django.db.models import Q
        qs = Employee.objects.all()
        if request.query_params.get('active') == '1':
            qs = qs.filter(is_active=True)
        source = request.query_params.get('source')
        if source in {c[0] for c in Employee.SOURCE_CHOICES}:
            qs = qs.filter(source=source)
        search = (request.query_params.get('search') or '').strip()
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(email__icontains=search) |
                           Q(department__icontains=search) | Q(employee_code__icontains=search))
        total = qs.count()
        try:
            limit = max(1, min(1000, int(request.query_params.get('limit', 200))))
        except (TypeError, ValueError):
            limit = 200
        results = [{
            'id': e.id, 'employee_code': e.employee_code, 'name': e.name, 'email': e.email,
            'department': e.department, 'designation': e.designation, 'location': e.location,
            'reporting_manager': e.reporting_manager, 'role': e.role,
            'source': e.source, 'is_active': e.is_active,
            'synced_at': e.synced_at.isoformat() if e.synced_at else None,
        } for e in qs[:limit]]
        return Response({
            'results': results, 'count': total,
            'from_directory': Employee.objects.filter(source='directory').count(),
            'added_here': Employee.objects.filter(source='manual').count(),
            'inactive': Employee.objects.filter(is_active=False).count(),
        })

    def delete(self, request):
        """Clear the directory copy. People added by hand survive by default.

        They are here precisely because they are not in HRMS, so a wipe that
        took them too would quietly undo work that cannot be recovered by
        syncing again. ?everything=1 to mean it.
        """
        if (err := require_role(request, 'super_admin')):
            return err
        qs = Employee.objects.all()
        if request.query_params.get('everything') != '1':
            qs = qs.filter(source='directory')
        n = qs.count()
        qs.delete()
        kept = Employee.objects.count()
        return Response({
            'message': f'Cleared {n} employee record(s).'
                       + (f' {kept} added by hand were kept.' if kept else ''),
            'deleted': n, 'kept': kept,
        })


class EmployeeRowView(APIView):
    """One person: correct their details, or take out somebody added by hand.

    A directory row is not editable here -- it would be overwritten by the
    next sync, which is a confusing way to lose work. Fix those in HRMS.
    """

    def patch(self, request, employee_id):
        if (err := require_role(request, 'super_admin')):
            return err
        row = Employee.objects.filter(id=employee_id).first()
        if not row:
            return Response({'error': 'Not on the list.'}, status=404)
        if row.source == 'directory':
            return Response({'error': f'{row.name} comes from the company directory, so a '
                                      f'sync would undo any change made here. Correct it in '
                                      f'HRMS instead.'}, status=400)
        d = request.data
        for field, cap in (('name', 200), ('department', 150), ('designation', 150),
                           ('location', 150), ('employee_code', 50)):
            if field in d:
                setattr(row, field, str(d[field] or '').strip()[:cap])
        if 'is_active' in d:
            row.is_active = bool(d['is_active'])
        row.save()
        return Response({'message': f'{row.name} updated.'})

    def delete(self, request, employee_id):
        if (err := require_role(request, 'super_admin')):
            return err
        row = Employee.objects.filter(id=employee_id).first()
        if not row:
            return Response({'error': 'Not on the list.'}, status=404)
        if row.source == 'directory':
            return Response({'error': f'{row.name} comes from the company directory. Removing '
                                      f'them here would only last until the next sync -- mark '
                                      f'them as left in HRMS.'}, status=400)
        name = row.name
        row.delete()
        return Response({'message': f'{name} removed.'})


class EmployeeSyncView(APIView):
    """Pull the employee master from the company directory.

    Replaces re-uploading a spreadsheet every time somebody joins or leaves.
    The directory (accounts.PortalUser) is synced from HRMS, so this is one
    step further along the same chain rather than a second copy of it.
    """

    def get(self, request):
        """What a sync would bring, before anyone clicks it."""
        if (err := require_role(request, 'super_admin')):
            return err
        from accounts.models import PortalUser
        available = PortalUser.objects.exclude(email='').count()
        last = (Employee.objects.filter(source='directory')
                .order_by('-synced_at').values_list('synced_at', flat=True).first())
        return Response({
            'available': available,
            'no_email': PortalUser.objects.filter(email='').count(),
            'here_from_directory': Employee.objects.filter(source='directory').count(),
            'here_by_hand': Employee.objects.filter(source='manual').count(),
            'last_synced_at': last.isoformat() if last else None,
        })

    def post(self, request):
        if (err := require_role(request, 'super_admin')):
            return err
        from ..directory import sync_from_directory
        result = sync_from_directory()
        bits = [f"{result['created']} added", f"{result['updated']} updated"]
        if result['deactivated']:
            bits.append(f"{result['deactivated']} marked as left")
        if result['skipped_no_email']:
            bits.append(f"{result['skipped_no_email']} skipped with no email address")
        return Response({'message': 'Directory synced — ' + ', '.join(bits) + '.', **result})


class EmployeeCreateView(APIView):
    """Add one person by hand — a contractor, a joiner not yet in HRMS.

    Marked as added here, so a sync updates everybody else around them and
    leaves this row alone.
    """

    def post(self, request):
        if (err := require_role(request, 'super_admin')):
            return err
        d = request.data
        email = str(d.get('email') or '').strip().lower()
        name = str(d.get('name') or '').strip()
        if not email or '@' not in email:
            return Response({'error': 'A valid email address is required — it is how '
                                      'this person will sign in.'}, status=400)
        if not name:
            return Response({'error': 'A name is required.'}, status=400)
        if Employee.objects.filter(email=email).exists():
            return Response({'error': f'{email} is already on the list.'}, status=400)

        e = Employee.objects.create(
            email=email, name=name[:200],
            employee_code=str(d.get('employee_code') or '').strip()[:50],
            department=str(d.get('department') or '').strip()[:150],
            designation=str(d.get('designation') or '').strip()[:150],
            location=str(d.get('location') or '').strip()[:150],
            reporting_manager=str(d.get('reporting_manager') or '').strip()[:200],
            source='manual', is_active=True)
        return Response({'message': f'{e.name} added.', 'id': e.id}, status=201)
