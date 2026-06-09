import pandas as pd
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser

from ..models import EOMEmployee, EOMCycle, EOMNomination
from ..serializers import EOMEmployeeSerializer, EOMCycleSerializer, EOMNominationSerializer


# ── Employee management ──────────────────────────────────────────────────────

class EOMEmployeeImportView(APIView):
    """POST /api/eom/employees/import/"""
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        file = request.FILES.get('file')
        if not file:
            return Response({'error': 'No file uploaded.'}, status=400)

        try:
            if file.name.lower().endswith('.csv'):
                try:
                    df = pd.read_csv(file)
                except UnicodeDecodeError:
                    file.seek(0)
                    df = pd.read_csv(file, encoding='latin1')
            else:
                df = pd.read_excel(file)

            df.columns = df.columns.astype(str).str.strip().str.lower().str.replace(' ', '_')
            df = df.fillna('')
        except Exception as e:
            return Response({'error': f'Could not read file: {str(e)}'}, status=400)

        COLUMN_MAP = {
            'employee_id': ['employee_id', 'emp_id', 'emp_code'],
            'name':        ['name', 'employee_name'],
            'email':       ['email', 'email_id'],
            'designation': ['designation', 'role'],
            'department':  ['department', 'dept'],
            'zone':        ['zone', 'location', 'unit', 'zone_/_location'],
            'hod_id':      ['hod_id', 'hod'],
            'user_type':   ['user_type', 'type', 'role_type'],
        }

        def get_col(df, aliases):
            for a in aliases:
                if a in df.columns:
                    return a
            return None

        created, updated, errors = 0, 0, []

        for i, row in df.iterrows():
            def val(field):
                col = get_col(df, COLUMN_MAP[field])
                return str(row[col]).strip() if col and str(row[col]).strip() else ''

            emp_id = val('employee_id')
            if not emp_id:
                errors.append(f'Row {i+2}: Missing employee_id'); continue

            name = val('name')
            if not name:
                errors.append(f'Row {i+2}: Missing name for {emp_id}'); continue

            raw_type = val('user_type').lower()
            if 'hr' in raw_type or 'admin' in raw_type:
                user_type = 'hr'
            elif 'hod' in raw_type or 'head' in raw_type:
                user_type = 'hod'
            elif 'panel' in raw_type:
                user_type = 'panel'
            else:
                user_type = 'employee'

            _, was_created = EOMEmployee.objects.update_or_create(
                employee_id=emp_id,
                defaults={
                    'name':        name,
                    'email':       val('email'),
                    'designation': val('designation'),
                    'department':  val('department'),
                    'zone':        val('zone'),
                    'hod_id':      val('hod_id'),
                    'user_type':   user_type,
                    'is_active':   True,
                },
            )
            if was_created: created += 1
            else: updated += 1

        return Response({
            'message': f'Import complete. {created} created, {updated} updated.',
            'created': created,
            'updated': updated,
            'errors':  errors,
        })


class EOMEmployeeListView(APIView):
    """GET /api/eom/employees/"""

    def get(self, request):
        qs = EOMEmployee.objects.filter(is_active=True)
        return Response(EOMEmployeeSerializer(qs, many=True).data)


# ── Cycle management ─────────────────────────────────────────────────────────

class EOMCycleListCreateView(APIView):
    """GET/POST /api/eom/cycles/"""

    def get(self, request):
        return Response(EOMCycleSerializer(EOMCycle.objects.all(), many=True).data)

    def post(self, request):
        s = EOMCycleSerializer(data=request.data)
        if s.is_valid():
            s.save()
            return Response(s.data, status=201)
        return Response(s.errors, status=400)


class EOMCycleUpdateView(APIView):
    """PATCH /api/eom/cycles/<cycle_id>/"""

    def patch(self, request, cycle_id):
        try:
            cycle = EOMCycle.objects.get(id=cycle_id)
        except EOMCycle.DoesNotExist:
            return Response({'error': 'Cycle not found.'}, status=404)
        s = EOMCycleSerializer(cycle, data=request.data, partial=True)
        if s.is_valid():
            s.save()
            return Response(s.data)
        return Response(s.errors, status=400)


# ── Nominations ──────────────────────────────────────────────────────────────

class EOMAllNominationsView(APIView):
    """GET /api/eom/all-nominations/?cycle_id=<id>"""

    def get(self, request):
        cycle_id = request.query_params.get('cycle_id')
        qs = EOMNomination.objects.select_related('employee', 'cycle')
        if cycle_id:
            qs = qs.filter(cycle_id=cycle_id)
        return Response(EOMNominationSerializer(qs, many=True).data)


class EOMOverviewView(APIView):
    """GET /api/eom/org/overview/?cycle_id=<id>"""

    def get(self, request):
        cycle_id = request.query_params.get('cycle_id')
        if not cycle_id:
            return Response({'error': 'cycle_id required.'}, status=400)

        total_employees = EOMEmployee.objects.filter(is_active=True).count()
        noms = EOMNomination.objects.filter(cycle_id=cycle_id)

        submitted      = noms.exclude(status='draft').count()
        hod_approved   = noms.filter(status__in=['hod_approved', 'panel_approved', 'panel_rejected', 'hr_finalized']).count()
        panel_approved = noms.filter(status__in=['panel_approved', 'hr_finalized']).count()
        finalized      = noms.filter(status='hr_finalized').count()
        winners        = noms.filter(is_winner=True).count()
        pending        = max(0, total_employees - submitted)

        return Response({
            'total_employees': total_employees,
            'submitted':       submitted,
            'hod_approved':    hod_approved,
            'panel_approved':  panel_approved,
            'hr_finalized':    finalized,
            'winners':         winners,
            'pending':         pending,
        })


class EOMHRFinalizeView(APIView):
    """PATCH /api/eom/nominations/<nom_id>/hr-finalize/"""

    def patch(self, request, nom_id):
        try:
            nom = EOMNomination.objects.get(id=nom_id)
        except EOMNomination.DoesNotExist:
            return Response({'error': 'Nomination not found.'}, status=404)

        nom.status          = 'hr_finalized'
        nom.hr_remarks      = request.data.get('hr_remarks', nom.hr_remarks)
        nom.is_winner       = request.data.get('is_winner', nom.is_winner)
        nom.hr_finalized_at = timezone.now()
        nom.save()
        return Response(EOMNominationSerializer(nom).data)


class EOMDatabaseResetView(APIView):
    """POST /api/eom/admin/reset-database/
    Clears all EOM data — nominations, OTP tokens, employees, and cycles.
    Same pattern as the appraisal ResetDatabaseView.
    """

    def post(self, request):
        try:
            from ..models import EOMNomination, EOMOTPToken, EOMEmployee, EOMCycle
            EOMNomination.objects.all().delete()
            EOMOTPToken.objects.all().delete()
            EOMEmployee.objects.all().delete()
            EOMCycle.objects.all().delete()
            return Response({'message': 'All EOM database tables successfully cleared!'})
        except Exception as e:
            return Response({'error': f'Reset failed: {str(e)}'}, status=500)
