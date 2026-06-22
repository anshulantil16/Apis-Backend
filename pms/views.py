"""PMS views — comprehensive HR data management with full Excel support."""
import io
import random
import openpyxl
from django.core.mail import send_mail
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from .models import PMSEmployee, PMSAuditLog, GRADE_META

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
        'self_score': float(e.self_score) if e.self_score is not None else None,
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
        return Response({'employees': [serialize_emp(e) for e in employees], 'summary': build_summary(employees)})

    def delete(self, request):
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
            'new designation type': 'new_designation_type', 'new employee type': 'new_designation_type',
            'department': 'department', 'dept': 'department',
            'location': 'location', 'zone': 'location',
            'new operational location': 'new_operational_location', 'est': 'new_operational_location',
            'sub category': 'sub_category', 'sub cat': 'sub_category',
            'cost centre': 'cost_centre', 'cost center': 'cost_centre', 'cc': 'cost_centre',
            'category': 'category',
            'hq': 'hq_location', 'hq location': 'hq_location',
            'payroll location': 'payroll_location',
            'cadre': 'cadre',
            'grade': 'grade', 'band': 'grade',
            'gender': 'gender',
            'qualification': 'qualification', 'qualifications': 'qualification',
            'dob': 'date_of_birth', 'date of birth': 'date_of_birth',
            'age': 'age',
            'doj': 'date_of_joining', 'date of joining': 'date_of_joining',
            'tenure in apis as on 31st mar-2026': 'tenure_years', 'tenure in apis as on 31-mar-2026': 'tenure_years', 'tenure': 'tenure_years',
            'reporting manager': 'reporting_manager', 'report manager': 'reporting_manager',
            'reporting id': 'reporting_manager_id', 'report id': 'reporting_manager_id',
            'hod name': 'hod_name',
            'hod id': 'hod_id', 'hod': 'hod_id',
            'last promotion (yr)': 'last_promotion_year', 'last promotion': 'last_promotion_year',
            'years not promoted before 2022': 'years_not_promoted',
            'current variable pay 31-mar-26': 'current_variable_pay',
            'fy 22-23 (ctc)': 'fy_2223_ctc', 'fy 22-23 ctc': 'fy_2223_ctc', 'fy 2223 ctc': 'fy_2223_ctc',
            'increment 22-23': 'fy_2223_increment',
            'fy 23-24 (ctc)': 'fy_2324_ctc', 'fy 23-24 ctc': 'fy_2324_ctc', 'fy 2324 ctc': 'fy_2324_ctc',
            'increment 23-24': 'fy_2324_increment',
            'fy 24-25 (ctc)': 'fy_2425_ctc', 'fy 24-25 ctc': 'fy_2425_ctc', 'fy 2425 ctc': 'fy_2425_ctc',
            'increment 24-25': 'fy_2425_increment',
            'fy 25-26 (current ctc)': 'current_ctc', 'fy 25-26 current ctc': 'current_ctc', 'current ctc': 'current_ctc', 'current ctc - 31-mar-26': 'current_ctc',
            'self score': 'self_score',
            'manager score': 'manager_score', 'mgr score': 'manager_score', 'manager score (0-100)': 'manager_score',
            'hod score': 'hod_score', 'hod score (0-100)': 'hod_score',
            'score range': 'score_range',
            'final score range': 'final_score_range',
            'performance rating': 'performance_rating',
            'rating': 'rating',
            'performance': 'performance',
            'promotion (y/n)': 'promoted', 'promotion': 'promoted',
            'level': 'level',
            'promotion readiness': 'promotion_readiness',
            'salary correction': 'salary_correction',
            'management discretion': 'management_discretion_pct', 'management discretion on': 'management_discretion_pct',
            'one time reward': 'on_time_reward',
            'redesignation': 'redesignation', 're-designation': 'redesignation',
            'revised ctc': 'revised_ctc',
            'increment %': 'increment_pct',
            'promotion %': 'promotion_pct',
            'total hike %': 'total_impact_pct',
            'total salary increment': 'total_salary_increment',
            'ctc fixed': 'ctc_fixed',
            'variable': 'variable_pay',
            'fy 26-27 total ctc': 'new_ctc', 'fy 26-27': 'new_ctc',
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
                    'fy_2223_ctc': sf(data.get('fy_2223_ctc')) * 12 if sf(data.get('fy_2223_ctc')) else None,
                    'fy_2324_ctc': sf(data.get('fy_2324_ctc')) * 12 if sf(data.get('fy_2324_ctc')) else None,
                    'fy_2425_ctc': sf(data.get('fy_2425_ctc')) * 12 if sf(data.get('fy_2425_ctc')) else None,
                    'current_ctc': (sf(data.get('current_ctc'), 0) or 0) * 12,
                    'fy_2223_growth_pct': sf(data.get('fy_2223_growth_pct')),
                    'fy_2324_growth_pct': sf(data.get('fy_2324_growth_pct')),
                    'fy_2425_growth_pct': sf(data.get('fy_2425_growth_pct')),
                    'self_score': ss(data.get('self_score')),
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

        fields_to_update = [
            'manager_score', 'hod_score', 'management_score', 'override_increment_pct',
            'override_grade', 'promoted', 'promotion_pct', 'management_discretion_pct',
            'on_time_reward', 'reward_amount', 'promotion_readiness', 'notes',
            'salary_correction', 'redesignation', 'self_score',
        ]

        for field in fields_to_update:
            if field in d:
                old_val = getattr(emp, field)
                if field in ('promoted', 'redesignation', 'on_time_reward'):
                    new_val = bool(d[field])
                elif field in ('manager_score', 'hod_score', 'management_score', 'self_score'):
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

        return Response(serialize_emp(emp))


class PMSTemplateView(APIView):
    def get(self, request):
        from django.http import HttpResponse
        from openpyxl.styles import PatternFill, Font, Alignment, Border, Side

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'PMS Import'

        headers = [
            'SR NO', 'Employee ID *', 'Employee Name *', 'Designation', 'New Designation',
            'Designation Type', 'Department', 'New Department', 'New Function', 'Zone', 'Sub Zone',
            'Cost Centre', 'Category', 'HQ', 'Payroll Location', 'Cadre', 'Grade', 'Gender',
            'Qualification', 'DOB', 'Age', 'DOJ', 'Tenure in APIS as on 31st Mar-2026',
            'Reporting Manager', 'Reporting ID', 'HOD Name', 'HOD ID',
            'Current CTC - 31-Mar-26 *', 'Current Variable Pay 31-Mar-26', 'Last Promotion (YR)', 'Years Not Promoted Before 2022',
            'FY 22-23 (CTC)', 'Increment 22-23', 'FY 23-24 (CTC)', 'Increment 23-24', 'FY 24-25 (CTC)', 'Increment 24-25', 'FY 25-26 (Current CTC)',
            'Self Score', 'Manager Score', 'HOD Score', 'Score Range', 'Final Score Range',
            'Rating', 'Performance', 'Promotion (Y/N)', 'Level', 'Promotion Readiness',
            'Salary Correction Level', 'Management Discretion', 'One Time Reward', 'Redesignation',
            'Revised CTC', 'Increment %', 'Promotion %', 'Total Hike %', 'Total Salary Increment',
            'CTC Fixed', 'Variable', 'FY 26-27 Total CTC',
            'Manager Remarks', 'HOD Remarks',
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
            # EMP001: Monthly 50000, Annual 600000, Grade A (10% increment), Promoted (8%), Mgmt 5% = 23% total
            [1, 'EMP001', 'Rahul Sharma', 'Sales Manager', 'Sr Sales Manager', 'STAT',
             'Sales', 'Sales-New', 'Direct Sales', 'Zone A', 'Sub Zone 1', 'CC001', 'CAT01', 'HQ Mumbai', 'Mumbai',
             'M1', 'A', 'Male', 'MBA', '1985-05-15', 38, '2015-06-01', 9,
             'Rajesh Kumar', 'MGR001', 'Priya Singh', 'HOD001',
             50000, 8000, 2024, 0,
             600000, 50000, 620000, 51667, 640000, 53333, 660000,
             82, 85, 88, '80-90', '85-95',
             85, 'High', 'Yes', 'L2', '1_year',
             'Normal', 10, 'Yes', 'No',
             660000, 10, 8, 5, 81180,
             660000, 25000, 811800,
             'Strong leader', 'Exceeded targets'],
            # EMP002: Monthly 40000, Annual 480000, Grade A+ (12% increment), Promoted (8%), Mgmt 10% = 30% total
            [2, 'EMP002', 'Priya Singh', 'Sr Executive', '', 'MANAGER',
             'Marketing', 'Marketing-New', 'Digital Marketing', 'Zone B', 'Sub Zone 2', 'CC002', 'CAT01', 'HQ Delhi', 'Delhi',
             'O1', 'A+', 'Female', 'B.Tech', '1992-08-22', 31, '2018-03-15', 7,
             'Ramesh Gupta', 'MGR002', 'Amit Kumar', 'HOD002',
             40000, 6000, 2022, 2,
             450000, 40000, 480000, 42667, 510000, 45333, 540000,
             88, 92, 90, '85-95', '90-100',
             92, 'Excellent', 'Yes', 'L1', 'ready_now',
             'High', 12, 'Yes', 'Yes',
             540000, 12, 8, 10, 168480,
             540000, 35000, 702000,
             'Exceptional performer', 'Ready for next level'],
            # EMP003: Monthly 25000, Annual 300000, Grade B (5% increment), Not promoted (0%), Mgmt 0% = 5% total
            [3, 'EMP003', 'Amit Kumar', 'Associate', '', '',
             'Operations', 'Operations-New', 'Process Ops', 'Zone C', 'Sub Zone 3', 'CC003', 'CAT02', 'HQ Chennai', 'Chennai',
             'W1', 'B', 'Male', 'B.Sc', '1998-12-10', 25, '2022-01-10', 2,
             'Vikram Singh', 'MGR003', 'Deepak Nair', 'HOD003',
             25000, 2500, None, 2,
             280000, 23333, 300000, 25000, 320000, 26667, 340000,
             65, 68, 70, '60-70', '65-75',
             68, 'Average', 'No', 'L3', 'not_ready',
             'Normal', 5, 'No', 'No',
             340000, 5, 0, 0, 17000,
             340000, 0, 357000,
             'Meets expectations', 'Needs development'],
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
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'PMS Results'

        headers = [
            'Emp ID', 'Name', 'Designation', 'Department', 'Band', 'Location',
            'Current CTC', 'Mgr Score', 'HOD Score', 'Mgt Score', 'Final Score',
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


class PMSLoginView(APIView):
    """PMS Admin login with OTP via email."""

    def post(self, request):
        action = request.data.get('action')
        email = request.data.get('email', '').lower().strip()

        if not email or '@' not in email:
            return Response({'error': 'Invalid email address'}, status=400)

        if action == 'send_otp':
            # Generate 4-digit OTP
            otp = str(random.randint(1000, 9999))

            # Store OTP in session (in production, use Redis or database)
            request.session[f'pms_otp_{email}'] = otp
            request.session.set_expiry(300)  # 5 minutes expiry

            # Send email
            try:
                send_mail(
                    subject='PMS Simulator - Verification Code',
                    message=f'Your PMS Simulator verification code is: {otp}\n\nThis code will expire in 5 minutes.',
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[email],
                    fail_silently=False,
                )
                return Response({
                    'success': True,
                    'message': f'OTP sent to {email}',
                    'note': 'Check your email for the verification code'
                })
            except Exception as e:
                return Response({
                    'error': f'Failed to send email: {str(e)}',
                    'note': 'Demo mode: OTP is 1234'
                }, status=500)

        elif action == 'verify_otp':
            otp = request.data.get('otp', '').strip()
            stored_otp = request.session.get(f'pms_otp_{email}')

            if not stored_otp:
                return Response({'error': 'OTP expired or not found. Request a new one.'}, status=400)

            if otp == stored_otp:
                # OTP verified - set session token
                request.session[f'pms_admin_{email}'] = True
                del request.session[f'pms_otp_{email}']
                return Response({
                    'success': True,
                    'message': 'Verified successfully!',
                    'token': email
                })
            else:
                return Response({'error': 'Invalid OTP'}, status=400)

        return Response({'error': 'Invalid action'}, status=400)
