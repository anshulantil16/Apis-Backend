"""PMS views — no sessions, single global employee pool."""
import io
import openpyxl
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from .models import PMSEmployee

GRADE_ORDER = ['A+', 'A', 'B+', 'B', 'C', 'D']


def serialize_emp(e):
    cfg = e.grade_config
    return {
        'id': e.id,
        'employee_id': e.employee_id,
        'name': e.name,
        'designation': e.designation,
        'department': e.department,
        'location': e.location,
        'band': e.band,
        'gender': e.gender,
        'fiscal_year': e.fiscal_year,
        'current_ctc': float(e.current_ctc),
        'manager_score': float(e.manager_score) if e.manager_score is not None else None,
        'hod_score': float(e.hod_score) if e.hod_score is not None else None,
        'management_score': float(e.management_score) if e.management_score is not None else None,
        'fy_prev1_score': float(e.fy_prev1_score) if e.fy_prev1_score is not None else None,
        'fy_prev2_score': float(e.fy_prev2_score) if e.fy_prev2_score is not None else None,
        'manager_remarks': e.manager_remarks,
        'hod_remarks': e.hod_remarks,
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
        'new_ctc': e.new_ctc,
        'promoted': e.promoted,
        'on_time_reward': e.on_time_reward,
        'management_discretion': e.management_discretion,
        'promotion_readiness': e.promotion_readiness,
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

    # Department breakdown
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

    # Grade increment breakdown
    grade_inc = {}
    for e in employees:
        g = e.effective_grade
        if g not in grade_inc:
            grade_inc[g] = {'total_inc': 0, 'count': 0, 'total_ctc': 0}
        grade_inc[g]['total_inc'] += e.increment_amount
        grade_inc[g]['count']     += 1
        grade_inc[g]['total_ctc'] += float(e.current_ctc)

    # Top 10 / Bottom 10
    sorted_emps = sorted(employees, key=lambda e: e.final_score, reverse=True)
    top10 = [serialize_emp(e) for e in sorted_emps[:10]]
    bot10 = [serialize_emp(e) for e in sorted_emps[-10:]]

    # Promotion readiness
    readiness = {'ready_now': 0, '1_year': 0, '2_years': 0, 'not_ready': 0}
    for e in employees:
        if e.promotion_readiness in readiness:
            readiness[e.promotion_readiness] += 1

    # Performance vs salary quadrant
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

    # Gender
    gender_map = {}
    for e in employees:
        g = e.gender or 'Not Specified'
        if g not in gender_map:
            gender_map[g] = {'count': 0, 'scores': []}
        gender_map[g]['count'] += 1
        gender_map[g]['scores'].append(e.final_score)
    gender_breakdown = [{'gender': g, 'count': v['count'], 'avg_score': round(sum(v['scores'])/len(v['scores']), 2)} for g, v in gender_map.items()]

    # Band breakdown
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

        HEADERS = [
            'employee_id', 'name', 'designation', 'department', 'location', 'band', 'gender',
            'fiscal_year', 'current_ctc', 'manager_score', 'hod_score', 'management_score',
            'fy_prev1_score', 'fy_prev2_score', 'manager_remarks', 'hod_remarks', 'promotion_readiness',
        ]

        created = updated = 0
        errors = []

        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not any(row):
                continue
            data = dict(zip(HEADERS, row))
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

            obj, was_created = PMSEmployee.objects.update_or_create(
                employee_id=emp_id,
                defaults={
                    'name': name,
                    'designation': str(data.get('designation') or '').strip(),
                    'department': str(data.get('department') or '').strip(),
                    'location': str(data.get('location') or '').strip(),
                    'band': str(data.get('band') or '').strip().upper()[:5],
                    'gender': str(data.get('gender') or '').strip(),
                    'fiscal_year': str(data.get('fiscal_year') or '2025-26').strip(),
                    'current_ctc': sf(data.get('current_ctc'), 0) or 0,
                    'manager_score': ss(data.get('manager_score')),
                    'hod_score': ss(data.get('hod_score')),
                    'management_score': ss(data.get('management_score')),
                    'fy_prev1_score': ss(data.get('fy_prev1_score')),
                    'fy_prev2_score': ss(data.get('fy_prev2_score')),
                    'manager_remarks': str(data.get('manager_remarks') or '').strip(),
                    'hod_remarks': str(data.get('hod_remarks') or '').strip(),
                    'promotion_readiness': str(data.get('promotion_readiness') or '').strip(),
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
        if 'override_increment_pct' in d:
            v = d['override_increment_pct']
            emp.override_increment_pct = float(v) if v not in (None, '', 'null') else None
        if 'override_grade' in d:
            emp.override_grade = d['override_grade'] or ''
        if 'promoted' in d:
            emp.promoted = bool(d['promoted'])
        if 'on_time_reward' in d:
            emp.on_time_reward = bool(d['on_time_reward'])
        if 'management_score' in d:
            v = d['management_score']
            emp.management_score = float(v) if v not in (None, '', 'null') else None
        if 'promotion_readiness' in d:
            emp.promotion_readiness = d['promotion_readiness'] or ''
        if 'notes' in d:
            emp.notes = d['notes']
        emp.save()
        return Response(serialize_emp(emp))


class PMSTemplateView(APIView):
    def get(self, request):
        from django.http import HttpResponse
        from openpyxl.styles import PatternFill, Font, Alignment, Border, Side

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'PMS Import'

        headers = [
            'Employee ID *', 'Employee Name *', 'Designation', 'Department', 'Location',
            'Band (D/C/M/O/W)', 'Gender', 'Fiscal Year', 'Current CTC (Annual INR) *',
            'Manager Score (0-100)', 'HOD Score (0-100)', 'Management Score (0-100)',
            'FY Prev-1 Score', 'FY Prev-2 Score',
            'Manager Remarks', 'HOD Remarks',
            'Promotion Readiness (ready_now / 1_year / 2_years / not_ready)',
        ]

        hf = PatternFill(start_color='4F46E5', end_color='4F46E5', fill_type='solid')
        for ci, h in enumerate(headers, 1):
            c = ws.cell(row=1, column=ci, value=h)
            c.fill = hf
            c.font = Font(color='FFFFFF', bold=True, size=10)
            c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)

        samples = [
            ['EMP001','Rahul Sharma','Sales Manager','Sales','Mumbai','M','Male','2025-26',720000,85,82,88,78,72,'Good performance','Strong team player','ready_now'],
            ['EMP002','Priya Singh','Sr. Executive','Marketing','Delhi','O','Female','2025-26',480000,92,90,94,88,85,'Exceptional','Outstanding','1_year'],
            ['EMP003','Amit Kumar','Associate','Operations','Chennai','W','Male','2025-26',300000,68,70,65,60,55,'Average','Meets targets','not_ready'],
        ]
        for ri, row in enumerate(samples, 2):
            for ci, val in enumerate(row, 1):
                ws.cell(row=ri, column=ci, value=val)

        ws2 = wb.create_sheet('Grade Guide')
        guide = [
            ['Grade','Score Range','Label','Increment %','Promotion %'],
            ['A+','≥ 106%','Exceptional','12–15%','10%'],
            ['A','95–100%','Outstanding','10–12%','8%'],
            ['B+','85–94%','Exceeds Target','7–10%','6%'],
            ['B','65–84%','Meets Target','4–7%','4%'],
            ['C','51–64%','Near Target','0–4%','0%'],
            ['D','<50%','Needs Improvement','2%','0%'],
        ]
        for ri, row in enumerate(guide, 1):
            for ci, val in enumerate(row, 1):
                c = ws2.cell(row=ri, column=ci, value=val)
                if ri == 1: c.font = Font(bold=True)

        widths = [14,22,18,16,14,18,10,12,22,20,18,22,14,14,28,22,40]
        for ci, w in enumerate(widths, 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = w
        ws.row_dimensions[1].height = 44

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        resp = HttpResponse(buf.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = 'attachment; filename="PMS_Import_Template.xlsx"'
        return resp


class PMSExportView(APIView):
    def get(self, request):
        from django.http import HttpResponse
        from openpyxl.styles import PatternFill, Font, Alignment

        employees = list(PMSEmployee.objects.all())
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'PMS Results'

        headers = ['Emp ID','Name','Designation','Department','Band','Current CTC','Mgr Score','HOD Score','Mgt Score','Final Score','Grade','Label','Increment %','Increment Amt','New CTC','Promoted','Reward','Notes']
        hf = PatternFill(start_color='4F46E5', end_color='4F46E5', fill_type='solid')
        for ci, h in enumerate(headers, 1):
            c = ws.cell(row=1, column=ci, value=h)
            c.fill = hf; c.font = Font(color='FFFFFF', bold=True)
            c.alignment = Alignment(horizontal='center')

        for ri, e in enumerate(employees, 2):
            for ci, val in enumerate([
                e.employee_id, e.name, e.designation, e.department, e.band,
                float(e.current_ctc),
                float(e.manager_score) if e.manager_score else '',
                float(e.hod_score) if e.hod_score else '',
                float(e.management_score) if e.management_score else '',
                e.final_score, e.effective_grade, e.grade_config['label'],
                e.effective_increment_pct, e.increment_amount, e.new_ctc,
                'Yes' if e.promoted else 'No',
                'Yes' if e.on_time_reward else 'No',
                e.notes,
            ], 1):
                ws.cell(row=ri, column=ci, value=val)

        buf = io.BytesIO()
        wb.save(buf); buf.seek(0)
        resp = HttpResponse(buf.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = 'attachment; filename="PMS_Results.xlsx"'
        return resp
