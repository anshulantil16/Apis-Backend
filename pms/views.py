"""PMS views — comprehensive HR data management with full Excel support."""
import io
import openpyxl
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from .models import PMSEmployee, PMSAuditLog, PMSSettings, OfferLetter, GRADE_META


def _apply_global_mgmt(employees):
    """Overlay the company-wide Management Score onto each employee instance (in memory
    only, not saved) so final_score / grade / increment all use the single value."""
    mgmt = PMSSettings.get_solo().management_score
    if mgmt is not None:
        for e in employees:
            e.management_score = mgmt
    return mgmt

GRADE_ORDER = ['A+', 'A', 'B+', 'B', 'C', 'D']


def serialize_emp(e):
    cfg = e.grade_config
    return {
        'id': e.id,
        'employee_id': e.employee_id,
        'name': e.name,
        'designation': e.designation,
        'new_designation': e.new_designation,
        'new_designation_type': e.new_designation_type,
        'cadre': e.cadre,
        'band': e.band,
        'level': e.level,
        'department': e.department,
        'business': e.business,
        'location': e.location,
        'payroll_location': e.payroll_location,
        'new_operational_location': e.new_operational_location,
        'sub_category': e.sub_category,
        'cost_centre': e.cost_centre,
        'category': e.category,
        'hq_location': e.hq_location,
        'gender': e.gender,
        'qualification': e.qualification,
        'date_of_birth': str(e.date_of_birth) if e.date_of_birth else None,
        'date_of_joining': str(e.date_of_joining) if e.date_of_joining else None,
        'age': e.age,
        'tenure_years': e.tenure_years,
        'reporting_manager': e.reporting_manager,
        'reporting_manager_id': e.reporting_manager_id,
        'hod_name': e.hod_name,
        'hod_id': e.hod_id,
        'fiscal_year': e.fiscal_year,
        'fy_2223_ctc': float(e.fy_2223_ctc) if e.fy_2223_ctc else None,
        'fy_2324_ctc': float(e.fy_2324_ctc) if e.fy_2324_ctc else None,
        'fy_2425_ctc': float(e.fy_2425_ctc) if e.fy_2425_ctc else None,
        'current_ctc': float(e.current_ctc),
        'fy_2223_growth_pct': float(e.fy_2223_growth_pct) if e.fy_2223_growth_pct else None,
        'fy_2324_growth_pct': float(e.fy_2324_growth_pct) if e.fy_2324_growth_pct else None,
        'fy_2425_growth_pct': float(e.fy_2425_growth_pct) if e.fy_2425_growth_pct else None,
        'emp_score': float(e.emp_score) if e.emp_score is not None else None,
        'manager_score': float(e.manager_score) if e.manager_score is not None else None,
        'hod_score': float(e.hod_score) if e.hod_score is not None else None,
        'management_score': float(e.management_score) if e.management_score is not None else None,
        'fy_2223_grade': e.fy_2223_grade,
        'fy_2324_grade': e.fy_2324_grade,
        'fy_2425_grade': e.fy_2425_grade,
        'last_promotion_year': e.last_promotion_year,
        'final_score': e.final_score,
        'auto_grade': e.auto_grade,
        'effective_grade': e.effective_grade,
        'override_grade': e.override_grade,
        'grade_label': cfg['label'],
        'grade_color': cfg['color'],
        'inc_min': cfg['inc_min'],
        'inc_max': cfg['inc_max'],
        'promo_pct': cfg['promo_pct'],
        'override_increment_pct': float(e.override_increment_pct) if e.override_increment_pct is not None else None,
        'effective_increment_pct': e.effective_increment_pct,
        'increment_amount': e.increment_amount,
        'promotion_pct': float(e.promotion_pct),
        'promotion_amount': e.promotion_amount,
        'management_discretion_pct': float(e.management_discretion_pct),
        'management_discretion_amount': e.management_discretion_amount,
        'salary_correction': float(e.salary_correction),
        'total_impact_pct': e.total_impact_pct,
        'new_ctc': e.new_ctc,
        'new_ctc_monthly': e.new_ctc_monthly,
        'promoted': e.promoted,
        'redesignation': e.redesignation,
        'on_time_reward': e.on_time_reward,
        'reward_amount': float(e.reward_amount),
        'promotion_readiness': e.promotion_readiness,
        'manager_remarks': e.manager_remarks,
        'hod_remarks': e.hod_remarks,
        'notes': e.notes,
    }


def build_summary(employees):
    total = len(employees)
    if total == 0:
        return {'total_employees': 0}

    total_ctc     = sum(float(e.current_ctc) for e in employees)
    total_new_ctc = sum(e.new_ctc for e in employees)
    total_inc     = total_new_ctc - total_ctc
    promoted      = sum(1 for e in employees if e.promoted)
    rewarded      = sum(1 for e in employees if e.on_time_reward)
    avg_score     = sum(e.final_score for e in employees) / total

    grade_dist = {}
    for e in employees:
        g = e.effective_grade
        grade_dist[g] = grade_dist.get(g, 0) + 1

    dept_map = {}
    for e in employees:
        d = e.department or 'Unknown'
        if d not in dept_map:
            dept_map[d] = {'current': 0, 'new': 0, 'count': 0, 'scores': [], 'promoted': 0}
        dept_map[d]['current'] += float(e.current_ctc)
        dept_map[d]['new']     += e.new_ctc
        dept_map[d]['count']   += 1
        dept_map[d]['scores'].append(e.final_score)
        if e.promoted:
            dept_map[d]['promoted'] += 1

    dept_breakdown = [{
        'department': d,
        'count': v['count'],
        'current_ctc': round(v['current'], 2),
        'new_ctc': round(v['new'], 2),
        'increment': round(v['new'] - v['current'], 2),
        'avg_score': round(sum(v['scores']) / len(v['scores']), 2),
        'promotion_cases': v['promoted'],
    } for d, v in sorted(dept_map.items())]

    grade_inc = {}
    for e in employees:
        g = e.effective_grade
        if g not in grade_inc:
            grade_inc[g] = {'total_inc': 0, 'count': 0, 'total_ctc': 0}
        grade_inc[g]['total_inc'] += e.increment_amount
        grade_inc[g]['count']     += 1
        grade_inc[g]['total_ctc'] += float(e.current_ctc)

    sorted_emps = sorted(employees, key=lambda e: e.final_score, reverse=True)
    top10 = [serialize_emp(e) for e in sorted_emps[:10]]
    bot10 = [serialize_emp(e) for e in sorted_emps[-10:]]

    readiness = {'ready_now': 0, '1_year': 0, '2_years': 0, 'not_ready': 0}
    for e in employees:
        if e.promotion_readiness in readiness:
            readiness[e.promotion_readiness] += 1

    scores = sorted(e.final_score for e in employees)
    ctcs   = sorted(float(e.current_ctc) for e in employees)
    med_score = scores[total // 2]
    med_ctc   = ctcs[total // 2]
    quadrants = {'high_perf_high_pay': 0, 'high_perf_low_pay': 0, 'low_perf_high_pay': 0, 'low_perf_low_pay': 0}
    for e in employees:
        hp = e.final_score >= med_score
        hs = float(e.current_ctc) >= med_ctc
        key = ('high' if hp else 'low') + '_perf_' + ('high' if hs else 'low') + '_pay'
        quadrants[key] += 1

    gender_map = {}
    for e in employees:
        g = e.gender or 'Not Specified'
        if g not in gender_map:
            gender_map[g] = {'count': 0, 'scores': []}
        gender_map[g]['count'] += 1
        gender_map[g]['scores'].append(e.final_score)
    gender_breakdown = [{'gender': g, 'count': v['count'], 'avg_score': round(sum(v['scores'])/len(v['scores']), 2)} for g, v in gender_map.items()]

    band_map = {}
    for e in employees:
        b = e.band or 'Unknown'
        if b not in band_map:
            band_map[b] = {'current': 0, 'new': 0, 'count': 0}
        band_map[b]['current'] += float(e.current_ctc)
        band_map[b]['new']     += e.new_ctc
        band_map[b]['count']   += 1

    return {
        'total_employees': total,
        'total_current_ctc': round(total_ctc, 2),
        'total_new_ctc': round(total_new_ctc, 2),
        'total_increment': round(total_inc, 2),
        'increment_pct': round((total_inc / total_ctc * 100) if total_ctc else 0, 2),
        'avg_score': round(avg_score, 2),
        'promoted_count': promoted,
        'reward_count': rewarded,
        'grade_distribution': grade_dist,
        'grade_increment_breakdown': {g: {
            'count': v['count'],
            'total_increment': round(v['total_inc'], 2),
            'avg_increment_pct': round((v['total_inc'] / v['total_ctc'] * 100) if v['total_ctc'] else 0, 2),
        } for g, v in grade_inc.items()},
        'department_breakdown': dept_breakdown,
        'band_breakdown': [{'band': b, 'count': v['count'], 'current_ctc': round(v['current'], 2), 'new_ctc': round(v['new'], 2)} for b, v in sorted(band_map.items())],
        'top10_performers': top10,
        'bottom10_performers': bot10,
        'promotion_readiness': readiness,
        'performance_vs_salary': quadrants,
        'gender_breakdown': gender_breakdown,
        'median_score': med_score,
        'median_ctc': med_ctc,
    }


class PMSListView(APIView):
    def get(self, request):
        employees = list(PMSEmployee.objects.all())
        _apply_global_mgmt(employees)
        return Response({'employees': [serialize_emp(e) for e in employees], 'summary': build_summary(employees)})

    def delete(self, request):
        # `pms_offerletter` is an orphaned table (no Django model) whose non-cascading
        # FK to pms_pmsemployee blocks employee deletion. Clear those rows first so the
        # employee delete (which cascades to audit logs) can succeed.
        from django.db import connection
        with connection.cursor() as cursor:
            try:
                cursor.execute("DELETE FROM pms_offerletter")
            except Exception:
                pass
        PMSEmployee.objects.all().delete()
        return Response({'message': 'All PMS data cleared.'})


class PMSImportView(APIView):
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        file = request.FILES.get('file')
        if not file:
            return Response({'error': 'No file provided.'}, status=400)
        try:
            wb = openpyxl.load_workbook(file, data_only=True)
            ws = wb.active
        except Exception as e:
            return Response({'error': f'Cannot read file: {str(e)}'}, status=400)

        HEADER_ALIASES = {
            'sr no': 'sr_no', 'sr. no': 'sr_no', 'sno': 'sr_no',
            'employee id': 'employee_id', 'emp id': 'employee_id', 'empid': 'employee_id', 'er no': 'employee_id',
            'employee name': 'name', 'name': 'name',
            'designation': 'designation', 'current designation': 'designation',
            'new designation': 'new_designation', 're-designation': 'new_designation',
            'new designation type': 'new_designation_type', 'new employee type': 'new_designation_type', 'designation type': 'new_designation_type',
            'department': 'department', 'dept': 'department',
            'new department': 'business', 'new function': 'business',
            'location': 'location', 'zone': 'location',
            'new operational location': 'new_operational_location', 'est': 'new_operational_location', 'sub zone': 'sub_category',
            'sub category': 'sub_category', 'sub cat': 'sub_category',
            'cost centre': 'cost_centre', 'cost center': 'cost_centre', 'cc': 'cost_centre',
            'category': 'category',
            'hq': 'hq_location', 'hq location': 'hq_location',
            'payroll location': 'payroll_location',
            'cadre': 'cadre',
            'band': 'band', 'grade': 'band',
            'level': 'level',
            'gender': 'gender',
            'qualification': 'qualification', 'qualifications': 'qualification',
            'dob': 'date_of_birth', 'date of birth': 'date_of_birth',
            'age': 'age',
            'doj': 'date_of_joining', 'date of joining': 'date_of_joining',
            'tenure': 'tenure_years', 'tenure in apis as on 31-mar-2026': 'tenure_years', 'tenure in apis as on 31st mar-2026': 'tenure_years',
            'reporting manager': 'reporting_manager', 'report manager': 'reporting_manager',
            'report id': 'reporting_manager_id', 'report no id': 'reporting_manager_id', 'reporting id': 'reporting_manager_id',
            'hod name': 'hod_name',
            'hod id': 'hod_id', 'hod': 'hod_id',
            'last promotion': 'last_promotion_year', 'last promotion (yr)': 'last_promotion_year', 'last promotion (year)': 'last_promotion_year',
            'fy 22-23 ctc': 'fy_2223_ctc', 'fy 2223 ctc': 'fy_2223_ctc', 'fy 22-23 (ctc)': 'fy_2223_ctc',
            'fy 23-24 ctc': 'fy_2324_ctc', 'fy 2324 ctc': 'fy_2324_ctc', 'fy 23-24 (ctc)': 'fy_2324_ctc',
            'fy 24-25 ctc': 'fy_2425_ctc', 'fy 2425 ctc': 'fy_2425_ctc', 'fy 24-25 (ctc)': 'fy_2425_ctc',
            'fy 25-26 current ctc': 'current_ctc', 'current ctc': 'current_ctc', 'ctc': 'current_ctc', 'current ctc (annual inr)': 'current_ctc',
            'fy 25-26 (current ctc)': 'current_ctc', 'current ctc - 31-mar-26': 'current_ctc', 'current ctc- 31-mar-26': 'current_ctc',
            'fy 22-23 (%)': 'fy_2223_growth_pct', 'fy 22-23 %': 'fy_2223_growth_pct',
            'fy 23-24 (%)': 'fy_2324_growth_pct', 'fy 23-24 %': 'fy_2324_growth_pct',
            'fy 24-25 (%)': 'fy_2425_growth_pct', 'fy 24-25 %': 'fy_2425_growth_pct',
            'self score': 'emp_score', 'emp score': 'emp_score', 'emp score (0-100)': 'emp_score',
            'manager score': 'manager_score', 'mgr score': 'manager_score', 'manager score (0-100)': 'manager_score',
            'hod score': 'hod_score', 'hod score (0-100)': 'hod_score',
            'fy 22-23 grade': 'fy_2223_grade',
            'fy 23-24 grade': 'fy_2324_grade',
            'fy 24-25 grade': 'fy_2425_grade',
            'score range': 'score_range',
            'final score range': 'final_score_range',
            'rating': 'rating',
            'performance': 'performance',
            'promotion (y/n)': 'promoted', 'promotion': 'promoted',
            'promotion readiness': 'promotion_readiness',
            'salary correction': 'salary_correction', 'salary correction level': 'salary_correction',
            'promotion %': 'promotion_pct',
            'management discretion': 'management_discretion_pct', 'management discretion %': 'management_discretion_pct',
            'one time reward': 'on_time_reward', 'one time reward': 'on_time_reward',
            'reward amount': 'reward_amount',
            'redesignation': 'redesignation', 're-designation': 'redesignation',
            'revised ctc': 'revised_ctc',
            'increment nt %': 'increment_nt_pct', 'increment nt': 'increment_nt_pct',
            'increment on %': 'promotion_pct', 'increment on': 'promotion_pct',
            'hike %': 'total_impact_pct', 'total hike %': 'total_impact_pct',
            'fy 26-27 ctc': 'new_ctc', 'fy 26-27': 'new_ctc',
            'management score': 'management_score', 'mgt score': 'management_score', 'management score (0-100)': 'management_score',
            'manager remarks': 'manager_remarks',
            'hod remarks': 'hod_remarks',
        }

        header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
        col_map = {}
        for ci, cell in enumerate(header_row):
            if cell is None:
                continue
            key = str(cell).strip().lower().replace('*', '').strip()
            field = HEADER_ALIASES.get(key)
            if field:
                col_map[field] = ci

        created = updated = 0
        errors = []

        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not any(row):
                continue
            data = {field: row[ci] for field, ci in col_map.items() if ci < len(row)}
            emp_id = str(data.get('employee_id') or '').strip()
            name   = str(data.get('name') or '').strip()
            if not emp_id or not name:
                errors.append(f'Row {row_idx}: Missing Employee ID or Name — skipped')
                continue

            def sf(val, default=None):
                if val is None or str(val).strip() == '': return default
                try: return round(float(str(val).replace(',', '')), 2)
                except: return default

            def ss(val):
                v = sf(val)
                return None if v is None else max(0.0, min(100.0, v))

            def parse_bool(val):
                if val is None or str(val).strip() == '': return False
                return str(val).lower().strip() in ('y', 'yes', 'true', '1')

            def parse_date(val):
                if val is None or str(val).strip() == '': return None
                try:
                    from datetime import datetime
                    if isinstance(val, str):
                        return datetime.strptime(val, '%Y-%m-%d').date()
                    else:
                        return val.date() if hasattr(val, 'date') else val
                except:
                    return None

            obj, was_created = PMSEmployee.objects.update_or_create(
                employee_id=emp_id,
                defaults={
                    'name': name,
                    'designation': str(data.get('designation') or '').strip(),
                    'new_designation': str(data.get('new_designation') or '').strip(),
                    'new_designation_type': str(data.get('new_designation_type') or '').strip(),
                    'department': str(data.get('department') or '').strip(),
                    'location': str(data.get('location') or '').strip(),
                    'new_operational_location': str(data.get('new_operational_location') or '').strip(),
                    'sub_category': str(data.get('sub_category') or '').strip(),
                    'cost_centre': str(data.get('cost_centre') or '').strip(),
                    'category': str(data.get('category') or '').strip(),
                    'hq_location': str(data.get('hq_location') or '').strip(),
                    'payroll_location': str(data.get('payroll_location') or '').strip(),
                    'cadre': str(data.get('cadre') or '').strip(),
                    'band': str(data.get('band') or '').strip().upper()[:5],
                    'level': str(data.get('level') or '').strip(),
                    'gender': str(data.get('gender') or '').strip(),
                    'qualification': str(data.get('qualification') or '').strip(),
                    'date_of_birth': parse_date(data.get('date_of_birth')),
                    'date_of_joining': parse_date(data.get('date_of_joining')),
                    'reporting_manager': str(data.get('reporting_manager') or '').strip(),
                    'reporting_manager_id': str(data.get('reporting_manager_id') or '').strip(),
                    'hod_name': str(data.get('hod_name') or '').strip(),
                    'hod_id': str(data.get('hod_id') or '').strip(),
                    'fiscal_year': str(data.get('fiscal_year') or '2025-26').strip(),
                    'fy_2223_ctc': sf(data.get('fy_2223_ctc')),
                    'fy_2324_ctc': sf(data.get('fy_2324_ctc')),
                    'fy_2425_ctc': sf(data.get('fy_2425_ctc')),
                    'current_ctc': sf(data.get('current_ctc'), 0) or 0,
                    'fy_2223_growth_pct': sf(data.get('fy_2223_growth_pct')),
                    'fy_2324_growth_pct': sf(data.get('fy_2324_growth_pct')),
                    'fy_2425_growth_pct': sf(data.get('fy_2425_growth_pct')),
                    'emp_score': ss(data.get('emp_score')),
                    'manager_score': ss(data.get('manager_score')),
                    'hod_score': ss(data.get('hod_score')),
                    'fy_2223_grade': str(data.get('fy_2223_grade') or '').strip(),
                    'fy_2324_grade': str(data.get('fy_2324_grade') or '').strip(),
                    'fy_2425_grade': str(data.get('fy_2425_grade') or '').strip(),
                    'last_promotion_year': int(data.get('last_promotion_year')) if data.get('last_promotion_year') else None,
                    'management_score': ss(data.get('management_score')),
                    'promoted': parse_bool(data.get('promoted')),
                    'promotion_pct': sf(data.get('promotion_pct'), 0) or 0,
                    'redesignation': parse_bool(data.get('redesignation')),
                    'on_time_reward': parse_bool(data.get('on_time_reward')),
                    'reward_amount': sf(data.get('reward_amount'), 0) or 0,
                    'management_discretion_pct': sf(data.get('management_discretion_pct'), 0) or 0,
                    'salary_correction': sf(data.get('salary_correction'), 0) or 0,
                    'promotion_readiness': str(data.get('promotion_readiness') or '').strip(),
                    'manager_remarks': str(data.get('manager_remarks') or '').strip(),
                    'hod_remarks': str(data.get('hod_remarks') or '').strip(),
                }
            )
            created += was_created
            updated += not was_created

        employees = list(PMSEmployee.objects.all())
        return Response({
            'message': f'✅ Import complete! {created} new employees added, {updated} updated.',
            'created': created, 'updated': updated, 'errors': errors,
            'total': len(employees), 'summary': build_summary(employees),
        })


class PMSEmployeeUpdateView(APIView):
    def patch(self, request, emp_id):
        try:
            emp = PMSEmployee.objects.get(id=emp_id)
        except PMSEmployee.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)

        d = request.data
        simulate = d.get('simulate_only', False)
        logs = []

        # management_score is company-wide (see PMSSettings), not editable per employee.
        fields_to_update = [
            'manager_score', 'hod_score', 'override_increment_pct',
            'override_grade', 'promoted', 'promotion_pct', 'management_discretion_pct',
            'on_time_reward', 'reward_amount', 'promotion_readiness', 'notes',
            'salary_correction', 'redesignation', 'emp_score',
        ]

        for field in fields_to_update:
            if field in d:
                old_val = getattr(emp, field)
                if field in ('promoted', 'redesignation', 'on_time_reward'):
                    new_val = bool(d[field])
                elif field in ('manager_score', 'hod_score', 'emp_score'):
                    v = d[field]
                    new_val = float(v) if v not in (None, '', 'null') else None
                    if new_val is not None:
                        new_val = max(0.0, min(100.0, new_val))
                elif field in ('override_increment_pct', 'promotion_pct', 'management_discretion_pct', 'salary_correction', 'reward_amount'):
                    v = d[field]
                    new_val = float(v) if v not in (None, '', 'null') else (0 if field in ('promotion_pct', 'management_discretion_pct', 'salary_correction', 'reward_amount') else None)
                else:
                    new_val = d[field]

                if str(old_val) != str(new_val):
                    logs.append({'field': field, 'old_value': str(old_val), 'new_value': str(new_val)})
                setattr(emp, field, new_val)

        if not simulate:
            emp.save()
            for log in logs:
                PMSAuditLog.objects.create(employee=emp, **log)

        _apply_global_mgmt([emp])
        return Response(serialize_emp(emp))


class PMSSettingsView(APIView):
    """Get / set company-wide PMS settings (currently the single Management Score)."""
    def get(self, request):
        s = PMSSettings.get_solo()
        return Response({
            'management_score': float(s.management_score) if s.management_score is not None else None,
        })

    def post(self, request):
        s = PMSSettings.get_solo()
        v = request.data.get('management_score')
        if v in (None, '', 'null'):
            s.management_score = None
        else:
            s.management_score = max(0.0, min(100.0, float(v)))
        s.save()
        return Response({
            'management_score': float(s.management_score) if s.management_score is not None else None,
            'message': 'Management score updated for all employees.',
        })


class PMSTemplateView(APIView):
    def get(self, request):
        from django.http import HttpResponse
        from openpyxl.styles import PatternFill, Font, Alignment, Border, Side

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'PMS Import'

        headers = [
            'SR NO', 'Employee ID *', 'Employee Name *', 'Designation', 'New Designation',
            'Designation Type', 'Department', 'New Department', 'New Function', 'Zone',
            'Sub Zone', 'Cost Centre', 'Category', 'HQ', 'Payroll Location',
            'Cadre', 'Grade', 'Gender', 'Qualification', 'DOB', 'Age', 'DOJ',
            'Tenure in APIS as on 31st Mar-2026', 'Reporting Manager', 'Reporting ID', 'HOD Name', 'HOD ID',
            'Span of Control', 'Current CTC - 31-Mar-26 *', 'Current Variable Pay 31-Mar-26',
            'Last Promotion (YR)', 'Years Not Promoted Before 2022',
            'FY 22-23 (CTC)', 'Increment 22-23', 'FY 23-24 (CTC)', 'Increment 23-24',
            'FY 24-25 (CTC)', 'Increment 24-25', 'FY 22-23 (%)', 'FY 23-24 (%)', 'FY 24-25 (%)',
            'FY 25-26 (Current CTC)', 'Self Score', 'Manager Score', 'HOD Score',
            'Score Range', 'Final Score Range', 'Rating', 'Performance',
            'Promotion (Y/N)', 'Level', 'Promotion Readiness', 'Salary Correction Level',
            'Management Discretion', 'One Time Reward', 'Redesignation', 'Revised CTC',
            'Increment %', 'Promotion %', 'Total Hike %', 'Total Salary Increment',
            'CTC Fixed', 'Variable', 'FY 26-27 Total CTC', 'Manager Remarks', 'HOD Remarks',
        ]

        hf = PatternFill(start_color='2E75B6', end_color='2E75B6', fill_type='solid')
        hf_yellow = PatternFill(start_color='FFEB9C', end_color='FFEB9C', fill_type='solid')

        for ci, h in enumerate(headers, 1):
            c = ws.cell(row=1, column=ci, value=h)
            is_required = '*' in h
            is_new = 'New' in h or 'Operating' in h
            c.fill = hf_yellow if is_new else hf
            c.font = Font(color='FFFFFF' if not is_new else '000000', bold=True, size=9)
            c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)

        samples = [
            [1, 'EMP001', 'Rahul Sharma', 'Sales Manager', 'Sr Sales Manager',
             'STAT', 'Sales', 'Sales', 'Revenue', 'Mumbai',
             'West', 'CC001', 'CAT01', 'HQ Mumbai', 'Mumbai',
             'M1', 'M', 'Male', 'MBA', '1985-05-15', 38, '2015-06-01',
             9, 'Rajesh Kumar', 'MGR001', 'Priya Singh', 'HOD001',
             5, 720000, 50000,
             2024, 1,
             600000, 50000, 650000, 50000,
             700000, 20000, 8, 7.7, 7.7,
             720000, 82, 85, 88,
             '', '', '', '',
             'Yes', 'L2', '1_year', 0,
             5, 'No', 'No', '',
             '', 8, '', '',
             '', '', '', 'Performing well', 'Strong leader'],
            [2, 'EMP002', 'Priya Singh', 'Sr Executive', '',
             'MANAGER', 'Marketing', 'Marketing', 'Brand', 'Delhi',
             'North', 'CC002', 'CAT01', 'HQ Delhi', 'Delhi',
             'O1', 'O', 'Female', 'B.Tech', '1992-08-22', 31, '2018-03-15',
             7, 'Ramesh Gupta', 'MGR002', 'Amit Kumar', 'HOD002',
             3, 560000, 40000,
             2022, 0,
             450000, 30000, 480000, 40000,
             520000, 40000, 6.7, 8.3, 7.7,
             560000, 88, 92, 90,
             '', '', '', '',
             'Yes', 'L1', 'ready_now', 0,
             8, 'Yes', 'Yes', '',
             '', 10, '', '',
             '', '', '', 'Excellent performance', 'Promotion ready'],
            [3, 'EMP003', 'Amit Kumar', 'Associate', '',
             '', 'Operations', 'Operations', 'Logistics', 'Chennai',
             'South', 'CC003', 'CAT02', 'HQ Chennai', 'Chennai',
             'W1', 'W', 'Male', 'B.Sc', '1998-12-10', 25, '2022-01-10',
             2, 'Vikram Singh', 'MGR003', 'Deepak Nair', 'HOD003',
             0, 320000, 20000,
             None, 3,
             250000, 30000, 280000, 20000,
             300000, 20000, 12, 7.1, 7.1,
             320000, 65, 68, 70,
             '', '', '', '',
             'No', 'L3', 'not_ready', 0,
             0, 'No', 'No', '',
             '', 0, '', '',
             '', '', '', 'Adequate performance', 'New employee'],
        ]

        for ri, row in enumerate(samples, 2):
            for ci, val in enumerate(row, 1):
                ws.cell(row=ri, column=ci, value=val)

        ws2 = wb.create_sheet('Grade Guide')
        guide = [
            ['Grade', 'Score Range', 'Label', 'Increment %', 'Promotion %'],
            ['A+', '≥ 106%', 'Exceptional', '12–15%', '10%'],
            ['A', '95–100%', 'Outstanding', '10–12%', '8%'],
            ['B+', '85–94%', 'Exceeds Target', '7–10%', '6%'],
            ['B', '65–84%', 'Meets Target', '4–7%', '4%'],
            ['C', '51–64%', 'Near Target', '0–4%', '0%'],
            ['D', '<50%', 'Needs Improvement', '2%', '0%'],
        ]
        for ri, row in enumerate(guide, 1):
            for ci, val in enumerate(row, 1):
                c = ws2.cell(row=ri, column=ci, value=val)
                if ri == 1: c.font = Font(bold=True)

        widths = [8, 14, 18, 16, 16, 18, 14, 12, 20, 16, 12, 12, 16, 16, 10, 8, 8, 10, 14, 14, 10, 12, 14, 18, 12, 12, 10, 14, 14, 14, 16, 14, 14, 14, 16, 16, 16, 14, 14, 14, 14, 12, 12, 12, 16, 16, 16, 18, 18, 20, 20]
        for ci, w in enumerate(widths[:len(headers)], 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = w
        ws.row_dimensions[1].height = 55

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        resp = HttpResponse(buf.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = 'attachment; filename="PMS_Complete_Template.xlsx"'
        return resp


class PMSExportView(APIView):
    def get(self, request):
        from django.http import HttpResponse
        from openpyxl.styles import PatternFill, Font, Alignment

        employees = list(PMSEmployee.objects.all())
        _apply_global_mgmt(employees)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'PMS Results'

        headers = [
            'Emp ID', 'Name', 'Designation', 'Department', 'Band', 'Location',
            'Current CTC', 'EMP Score', 'Mgr Score', 'HOD Score', 'Mgt Score', 'Final Score',
            'Grade', 'Label', 'Increment %', 'Increment Amt', 'Promotion %',
            'Promotion Amt', 'Mgmt Discretion %', 'Mgmt Discretion Amt',
            'New CTC', 'New CTC (Monthly)', 'Promoted', 'Redesignation', 'Reward',
            'Promotion Readiness', 'Notes'
        ]

        hf = PatternFill(start_color='2E75B6', end_color='2E75B6', fill_type='solid')
        for ci, h in enumerate(headers, 1):
            c = ws.cell(row=1, column=ci, value=h)
            c.fill = hf
            c.font = Font(color='FFFFFF', bold=True)
            c.alignment = Alignment(horizontal='center')

        for ri, e in enumerate(employees, 2):
            for ci, val in enumerate([
                e.employee_id, e.name, e.designation, e.department, e.band, e.location,
                float(e.current_ctc),
                float(e.emp_score) if e.emp_score else '',
                float(e.manager_score) if e.manager_score else '',
                float(e.hod_score) if e.hod_score else '',
                float(e.management_score) if e.management_score else '',
                e.final_score, e.effective_grade, e.grade_config['label'],
                e.effective_increment_pct, e.increment_amount, float(e.promotion_pct),
                e.promotion_amount, float(e.management_discretion_pct),
                e.management_discretion_amount, e.new_ctc, e.new_ctc_monthly,
                'Yes' if e.promoted else 'No', 'Yes' if e.redesignation else 'No',
                'Yes' if e.on_time_reward else 'No', e.promotion_readiness, e.notes,
            ], 1):
                ws.cell(row=ri, column=ci, value=val)

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        resp = HttpResponse(buf.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = 'attachment; filename="PMS_Results_Complete.xlsx"'
        return resp


class OfferLetterTemplateView(APIView):
    """Generate Excel template for Offer Letter upload."""

    def get(self, request):
        from django.http import HttpResponse
        from openpyxl.styles import PatternFill, Font, Alignment, Border, Side

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Offer Letters'

        headers = [
            'SR NO', 'Employee ID *', 'Employee Name *', 'Email *', 'Department',
            'Current Designation', 'New Designation', 'Current CTC *', 'New CTC *',
            'Increment %', 'Promotion %', 'Performance Rating', 'Grade Label',
            'Effective Date *', 'Remarks',
        ]

        hf = PatternFill(start_color='2E75B6', end_color='2E75B6', fill_type='solid')
        hf_yellow = PatternFill(start_color='FFEB9C', end_color='FFEB9C', fill_type='solid')
        border = Border(left=Side(style='thin'), right=Side(style='thin'),
                        top=Side(style='thin'), bottom=Side(style='thin'))

        for ci, h in enumerate(headers, 1):
            c = ws.cell(row=1, column=ci, value=h)
            is_required = '*' in h
            c.fill = hf if is_required else hf_yellow
            c.font = Font(color='FFFFFF' if is_required else '000000', bold=True, size=10)
            c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            c.border = border

        samples = [
            [1, 'EMP001', 'Rahul Sharma', 'rahul.sharma@apis.com', 'Sales', 'Sales Manager', 'Senior Sales Manager', 600000, 660000, 10, 0, 'A', 'Outstanding', '2026-07-01', 'Excellent performer'],
            [2, 'EMP002', 'Priya Singh', 'priya.singh@apis.com', 'Operations', 'Executive', 'Senior Executive', 450000, 540000, 12, 8, 'A+', 'Exceptional', '2026-07-01', 'Ready for promotion'],
            [3, 'EMP003', 'Amit Kumar', 'amit.kumar@apis.com', 'IT', 'Associate', 'Associate', 280000, 340000, 5, 0, 'B', 'Meets Target', '2026-07-01', ''],
        ]

        for ri, row in enumerate(samples, 2):
            for ci, val in enumerate(row, 1):
                c = ws.cell(row=ri, column=ci, value=val)
                c.border = border
                if ci == 14:  # Effective Date column
                    c.number_format = 'yyyy-mm-dd'

        for i in range(1, len(headers) + 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = 18

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        resp = HttpResponse(buf.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = 'attachment; filename="OfferLetter_Template.xlsx"'
        return resp


class OfferLetterUploadView(APIView):
    """Generate offer-letter PDFs synchronously from an uploaded Excel file.
    Preview mode: PDFs are generated and stored, but emails are NOT sent."""
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        from .offer_letter import generate_offer_letter_pdf
        from datetime import datetime, date
        from django.core.files.base import ContentFile

        file = request.FILES.get('file')
        if not file:
            return Response({'error': 'No file provided.'}, status=400)
        try:
            wb = openpyxl.load_workbook(file, data_only=True)
            ws = wb.active
        except Exception as e:
            return Response({'error': f'Cannot read file: {str(e)}'}, status=400)

        HEADER_MAP = {
            'sr no': 'sr_no', 'employee id': 'employee_id', 'employee name': 'name',
            'email': 'email', 'department': 'department',
            'current designation': 'current_designation', 'new designation': 'new_designation',
            'current ctc': 'current_ctc', 'new ctc': 'new_ctc',
            'increment %': 'increment_pct', 'promotion %': 'promotion_pct',
            'performance rating': 'performance_rating', 'grade label': 'grade_label',
            'effective date': 'effective_date', 'remarks': 'remarks',
        }
        header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
        col_map = {}
        for ci, cell in enumerate(header_row):
            if cell is None:
                continue
            key = str(cell).strip().lower().replace('*', '').strip()
            if key in HEADER_MAP:
                col_map[HEADER_MAP[key]] = ci

        required = ['employee_id', 'name', 'email', 'current_ctc', 'new_ctc', 'effective_date']
        if not all(f in col_map for f in required):
            return Response({'error': 'Missing required columns',
                             'required': ['Employee ID', 'Employee Name', 'Email', 'Current CTC', 'New CTC', 'Effective Date'],
                             'mapped': list(col_map.keys())}, status=400)

        def get_val(row, field, default=None):
            if field not in col_map:
                return default
            ci = col_map[field]
            return row[ci] if ci < len(row) else default

        def sf(val, default=0):
            if val is None or str(val).strip() == '':
                return default
            try:
                return float(str(val).replace(',', ''))
            except Exception:
                return default

        def format_date(val):
            if val is None or str(val).strip() == '':
                return None
            if isinstance(val, datetime):
                return val.date()
            if isinstance(val, date):
                return val
            for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y'):
                try:
                    return datetime.strptime(str(val).strip(), fmt).date()
                except Exception:
                    pass
            return None

        created = 0
        errors = []
        results = []
        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not any(row):
                continue
            emp_id = str(get_val(row, 'employee_id') or '').strip()
            name = str(get_val(row, 'name') or '').strip()
            email = str(get_val(row, 'email') or '').strip()
            if not emp_id or not name:
                continue
            try:
                # Standalone system — everything comes from the uploaded file, no PMS lookup.
                current_ctc = sf(get_val(row, 'current_ctc'))
                new_ctc = sf(get_val(row, 'new_ctc'))
                increment_pct = sf(get_val(row, 'increment_pct'))
                promotion_pct = sf(get_val(row, 'promotion_pct'))
                current_designation = str(get_val(row, 'current_designation') or '').strip()
                new_designation = str(get_val(row, 'new_designation') or '').strip()
                performance_rating = str(get_val(row, 'performance_rating') or '').strip()
                grade_label = str(get_val(row, 'grade_label') or '').strip()
                department = str(get_val(row, 'department') or '').strip()
                effective_date = format_date(get_val(row, 'effective_date')) or date.today()

                letter_type = 'increment'
                if new_designation and new_designation != current_designation:
                    letter_type = 'promotion' if promotion_pct > 0 else 'redesignation'
                if increment_pct > 0 and promotion_pct > 0:
                    letter_type = 'combined'

                offer = OfferLetter.objects.create(
                    employee=None, employee_code=emp_id, employee_name=name,
                    letter_type=letter_type,
                    current_ctc=current_ctc, new_ctc=new_ctc,
                    increment_pct=increment_pct, promotion_pct=promotion_pct,
                    effective_date=effective_date,
                    old_designation=current_designation, new_designation=new_designation,
                    performance_rating=performance_rating, grade_label=grade_label,
                    email_address=email, department=department, status='pending',
                )

                pdf_buf = generate_offer_letter_pdf(
                    None, current_ctc, new_ctc, increment_pct, promotion_pct, effective_date,
                    old_designation=current_designation, new_designation=new_designation,
                    performance_rating=performance_rating, grade_label=grade_label,
                    employee_id=emp_id, employee_name=name, department=department,
                )
                offer.pdf_file.save(f'offer_{emp_id}_{offer.id}.pdf', ContentFile(pdf_buf.read()), save=True)

                results.append({'employee_id': emp_id, 'name': name, 'status': 'generated',
                                'message': 'Letter generated (preview — not emailed)',
                                'pdf_url': f'/api/pms/offer-letter/{offer.id}/pdf/'})
                created += 1
            except Exception as e:
                errors.append(f'Row {row_idx}: {str(e)}')
                results.append({'employee_id': emp_id, 'name': name, 'status': 'failed', 'message': str(e)})

        return Response({
            'message': f'✅ {created} offer letter(s) generated (preview mode — emails not sent).',
            'created': created, 'errors': errors, 'results': results,
        })


class OfferLetterPDFView(APIView):
    """Download/view a generated offer-letter PDF."""
    def get(self, request, offer_letter_id):
        from django.http import FileResponse
        try:
            offer = OfferLetter.objects.get(id=offer_letter_id)
        except OfferLetter.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)
        if not offer.pdf_file:
            return Response({'error': 'No PDF generated for this letter'}, status=404)
        code = offer.employee_code or (offer.employee.employee_id if offer.employee else offer.id)
        return FileResponse(offer.pdf_file.open('rb'), content_type='application/pdf',
                            filename=f'OfferLetter_{code}.pdf')
