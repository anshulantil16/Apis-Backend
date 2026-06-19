"""
PMS Simulator API views.
"""
import io
import openpyxl
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser

from .models import PMSSession, PMSEmployee


# ── Helpers ───────────────────────────────────────────────────────────────────

def serialize_employee(emp):
    cfg = emp.grade_config
    return {
        'id': emp.id,
        'employee_id': emp.employee_id,
        'name': emp.name,
        'designation': emp.designation,
        'department': emp.department,
        'band': emp.band,
        'current_ctc': float(emp.current_ctc),
        'manager_score': float(emp.manager_score) if emp.manager_score is not None else None,
        'hod_score': float(emp.hod_score) if emp.hod_score is not None else None,
        'management_score': float(emp.management_score) if emp.management_score is not None else None,
        'manager_remarks': emp.manager_remarks,
        'hod_remarks': emp.hod_remarks,
        'final_score': emp.final_score,
        'auto_grade': emp.auto_grade,
        'effective_grade': emp.effective_grade,
        'override_grade': emp.override_grade,
        'grade_label': cfg['label'],
        'grade_color': cfg['color'],
        'inc_min': cfg['inc_min'],
        'inc_max': cfg['inc_max'],
        'promo_pct': cfg['promo_pct'],
        'override_increment_pct': float(emp.override_increment_pct) if emp.override_increment_pct is not None else None,
        'effective_increment_pct': emp.effective_increment_pct,
        'increment_amount': emp.increment_amount,
        'new_ctc': emp.new_ctc,
        'promoted': emp.promoted,
        'on_time_reward': emp.on_time_reward,
        'management_discretion': emp.management_discretion,
        'notes': emp.notes,
    }


def session_summary(session):
    employees = list(session.employees.all())
    total_employees = len(employees)
    total_current_ctc = sum(float(e.current_ctc) for e in employees)
    total_new_ctc = sum(e.new_ctc for e in employees)
    total_increment = total_new_ctc - total_current_ctc

    grade_dist = {}
    dept_dist = {}
    for e in employees:
        g = e.effective_grade
        grade_dist[g] = grade_dist.get(g, 0) + 1
        d = e.department or 'Unknown'
        if d not in dept_dist:
            dept_dist[d] = {'current': 0, 'new': 0, 'count': 0}
        dept_dist[d]['current'] += float(e.current_ctc)
        dept_dist[d]['new'] += e.new_ctc
        dept_dist[d]['count'] += 1

    promoted_count = sum(1 for e in employees if e.promoted)
    reward_count = sum(1 for e in employees if e.on_time_reward)

    return {
        'total_employees': total_employees,
        'total_current_ctc': round(total_current_ctc, 2),
        'total_new_ctc': round(total_new_ctc, 2),
        'total_increment': round(total_increment, 2),
        'increment_pct': round((total_increment / total_current_ctc * 100) if total_current_ctc else 0, 2),
        'promoted_count': promoted_count,
        'reward_count': reward_count,
        'grade_distribution': grade_dist,
        'department_breakdown': [
            {
                'department': d,
                'count': v['count'],
                'current_ctc': round(v['current'], 2),
                'new_ctc': round(v['new'], 2),
                'increment': round(v['new'] - v['current'], 2),
            }
            for d, v in sorted(dept_dist.items())
        ],
    }


# ── Session Views ──────────────────────────────────────────────────────────────

class SessionListCreateView(APIView):
    def get(self, request):
        sessions = PMSSession.objects.all()
        return Response([{
            'id': s.id,
            'name': s.name,
            'fiscal_year': s.fiscal_year,
            'is_active': s.is_active,
            'notes': s.notes,
            'created_at': s.created_at,
            'employee_count': s.employees.count(),
        } for s in sessions])

    def post(self, request):
        s = PMSSession.objects.create(
            name=request.data.get('name', 'New Session'),
            fiscal_year=request.data.get('fiscal_year', '2025-26'),
            notes=request.data.get('notes', ''),
        )
        return Response({'id': s.id, 'name': s.name}, status=201)


class SessionDetailView(APIView):
    def get(self, request, session_id):
        try:
            s = PMSSession.objects.get(id=session_id)
        except PMSSession.DoesNotExist:
            return Response({'error': 'Session not found'}, status=404)
        employees = [serialize_employee(e) for e in s.employees.all()]
        return Response({
            'id': s.id,
            'name': s.name,
            'fiscal_year': s.fiscal_year,
            'notes': s.notes,
            'employees': employees,
            'summary': session_summary(s),
        })

    def delete(self, request, session_id):
        try:
            PMSSession.objects.get(id=session_id).delete()
        except PMSSession.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)
        return Response({'message': 'Deleted'})


# ── Import View ────────────────────────────────────────────────────────────────

class ImportEmployeesView(APIView):
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request, session_id):
        try:
            session = PMSSession.objects.get(id=session_id)
        except PMSSession.DoesNotExist:
            return Response({'error': 'Session not found'}, status=404)

        file = request.FILES.get('file')
        if not file:
            return Response({'error': 'No file provided'}, status=400)

        try:
            wb = openpyxl.load_workbook(file, data_only=True)
            ws = wb.active

            HEADERS = [
                'employee_id', 'name', 'designation', 'department', 'band',
                'current_ctc', 'manager_score', 'hod_score', 'management_score',
                'manager_remarks', 'hod_remarks',
            ]

            created = 0
            updated = 0
            errors = []

            for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
                if not any(row):
                    continue
                data = dict(zip(HEADERS, row))

                emp_id = str(data.get('employee_id') or '').strip()
                name = str(data.get('name') or '').strip()
                if not emp_id or not name:
                    errors.append(f"Row {row_idx}: Missing Employee ID or Name")
                    continue

                try:
                    ctc = float(str(data.get('current_ctc') or 0).replace(',', ''))
                except (ValueError, TypeError):
                    errors.append(f"Row {row_idx}: Invalid CTC value")
                    continue

                def safe_score(val):
                    try:
                        v = float(str(val).replace(',', ''))
                        return max(0, min(100, v))
                    except (ValueError, TypeError):
                        return None

                obj, was_created = PMSEmployee.objects.update_or_create(
                    session=session,
                    employee_id=emp_id,
                    defaults={
                        'name': name,
                        'designation': str(data.get('designation') or '').strip(),
                        'department': str(data.get('department') or '').strip(),
                        'band': str(data.get('band') or '').strip().upper()[:5],
                        'current_ctc': ctc,
                        'manager_score': safe_score(data.get('manager_score')),
                        'hod_score': safe_score(data.get('hod_score')),
                        'management_score': safe_score(data.get('management_score')),
                        'manager_remarks': str(data.get('manager_remarks') or '').strip(),
                        'hod_remarks': str(data.get('hod_remarks') or '').strip(),
                    }
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        except Exception as e:
            return Response({'error': f'Failed to parse file: {str(e)}'}, status=400)

        return Response({
            'message': f'Import complete. {created} created, {updated} updated.',
            'created': created,
            'updated': updated,
            'errors': errors,
            'summary': session_summary(session),
        })


# ── Template Download ──────────────────────────────────────────────────────────

class DownloadTemplateView(APIView):
    def get(self, request):
        from django.http import HttpResponse

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'PMS Import'

        headers = [
            'Employee ID', 'Employee Name', 'Designation', 'Department',
            'Band (D/C/M/O/W)', 'Current CTC (Annual INR)',
            'Manager Score (0-100)', 'HOD Score (0-100)', 'Management Score (0-100)',
            'Manager Remarks', 'HOD Remarks',
        ]

        from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
        header_fill = PatternFill(start_color='1e293b', end_color='1e293b', fill_type='solid')
        header_font = Font(color='FFFFFF', bold=True, size=11)
        border = Border(
            left=Side(style='thin', color='334155'),
            right=Side(style='thin', color='334155'),
            top=Side(style='thin', color='334155'),
            bottom=Side(style='thin', color='334155'),
        )

        for ci, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=ci, value=h)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            cell.border = border

        # Sample rows
        samples = [
            ['EMP001', 'Rahul Sharma', 'Sales Manager', 'Sales', 'M', 600000, 85, 80, 90, 'Good performance', 'Consistent'],
            ['EMP002', 'Priya Singh', 'Sr. Executive', 'Marketing', 'O', 420000, 92, 88, 85, 'Excellent', 'Outstanding'],
            ['EMP003', 'Amit Kumar', 'Associate', 'Operations', 'W', 280000, 70, 72, 68, 'Average', 'Needs improvement'],
        ]
        sample_fill = PatternFill(start_color='f8fafc', end_color='f8fafc', fill_type='solid')
        for ri, row in enumerate(samples, 2):
            for ci, val in enumerate(row, 1):
                cell = ws.cell(row=ri, column=ci, value=val)
                cell.fill = sample_fill
                cell.border = border
                cell.alignment = Alignment(vertical='center')

        # Notes sheet
        ws2 = wb.create_sheet('Notes & Grade Guide')
        notes = [
            ['GRADE CRITERIA'],
            ['Grade', 'Score Range', 'Label', 'Increment %', 'Promotion %'],
            ['A+', '≥ 106%', 'Exceptional', '12-15%', '10%'],
            ['A',  '95-100%', 'Outstanding', '10-12%', '8%'],
            ['B+', '85-94%', 'Exceeds Target', '7-10%', '6%'],
            ['B',  '65-84%', 'Meets Target', '4-7%', '4%'],
            ['C',  '51-64%', 'Near Target', '0-4%', '0%'],
            ['D',  '<50%', 'Needs Improvement', '2%', '0%'],
            [],
            ['SCORE FORMULA'],
            ['Final Score = (Manager Score × 35%) + (HOD Score × 35%) + (Management Score × 30%)'],
            [],
            ['BAND CODES'],
            ['D = Director Band', 'C = CXO/HOD/Leader', 'M = Middle Management'],
            ['O = Officer/Supervisor', 'W = Workforce/Associate'],
        ]
        for ri, row in enumerate(notes, 1):
            for ci, val in enumerate(row, 1):
                ws2.cell(row=ri, column=ci, value=val)

        col_widths = [12, 25, 22, 18, 18, 22, 20, 18, 22, 25, 25]
        for ci, w in enumerate(col_widths, 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = w
        ws.row_dimensions[1].height = 40

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        response = HttpResponse(
            buf.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = 'attachment; filename="PMS_Import_Template.xlsx"'
        return response


# ── Employee Update ────────────────────────────────────────────────────────────

class EmployeeUpdateView(APIView):
    def patch(self, request, emp_id):
        try:
            emp = PMSEmployee.objects.get(id=emp_id)
        except PMSEmployee.DoesNotExist:
            return Response({'error': 'Employee not found'}, status=404)

        data = request.data
        if 'override_increment_pct' in data:
            val = data['override_increment_pct']
            emp.override_increment_pct = float(val) if val not in (None, '', 'null') else None
        if 'override_grade' in data:
            emp.override_grade = data['override_grade'] or ''
        if 'promoted' in data:
            emp.promoted = bool(data['promoted'])
        if 'on_time_reward' in data:
            emp.on_time_reward = bool(data['on_time_reward'])
        if 'management_discretion' in data:
            emp.management_discretion = bool(data['management_discretion'])
        if 'management_score' in data:
            val = data['management_score']
            emp.management_score = float(val) if val not in (None, '', 'null') else None
        if 'notes' in data:
            emp.notes = data['notes']
        emp.save()

        return Response(serialize_employee(emp))


# ── Bulk Update (apply same increment to grade) ────────────────────────────────

class BulkUpdateView(APIView):
    def post(self, request, session_id):
        try:
            session = PMSSession.objects.get(id=session_id)
        except PMSSession.DoesNotExist:
            return Response({'error': 'Session not found'}, status=404)

        grade = request.data.get('grade')
        increment_pct = request.data.get('increment_pct')
        action = request.data.get('action')  # 'set_increment', 'reset', 'promote_grade', 'reward_grade'

        employees = session.employees.all()
        if grade:
            employees = [e for e in employees if e.effective_grade == grade]

        updated = 0
        for emp in employees:
            if action == 'set_increment' and increment_pct is not None:
                emp.override_increment_pct = float(increment_pct)
                emp.save()
                updated += 1
            elif action == 'reset':
                emp.override_increment_pct = None
                emp.override_grade = ''
                emp.save()
                updated += 1
            elif action == 'promote_grade':
                emp.promoted = True
                emp.save()
                updated += 1
            elif action == 'reward_grade':
                emp.on_time_reward = True
                emp.save()
                updated += 1

        return Response({
            'updated': updated,
            'summary': session_summary(session),
        })


# ── Export Results ─────────────────────────────────────────────────────────────

class ExportResultsView(APIView):
    def get(self, request, session_id):
        from django.http import HttpResponse
        from openpyxl.styles import PatternFill, Font, Alignment, Border, Side

        try:
            session = PMSSession.objects.get(id=session_id)
        except PMSSession.DoesNotExist:
            return Response({'error': 'Session not found'}, status=404)

        wb = openpyxl.Workbook()

        # ── Sheet 1: Employee Results ──────────────────────────────────────────
        ws = wb.active
        ws.title = 'PMS Results'

        headers = [
            'Employee ID', 'Name', 'Designation', 'Department', 'Band',
            'Current CTC', 'Mgr Score', 'HOD Score', 'Mgmt Score', 'Final Score',
            'Grade', 'Grade Label', 'Increment %', 'Increment Amt', 'New CTC',
            'Promoted', 'On-Time Reward', 'Mgmt Discretion', 'Notes',
        ]

        GRADE_COLORS = {
            'A+': '16a34a', 'A': '22c55e', 'B+': 'ca8a04',
            'B': 'ea580c', 'C': 'dc2626', 'D': '7f1d1d',
        }

        hdr_fill = PatternFill(start_color='0f172a', end_color='0f172a', fill_type='solid')
        hdr_font = Font(color='FFFFFF', bold=True, size=10)
        thin = Border(
            left=Side(style='thin', color='e2e8f0'),
            right=Side(style='thin', color='e2e8f0'),
            top=Side(style='thin', color='e2e8f0'),
            bottom=Side(style='thin', color='e2e8f0'),
        )

        for ci, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=ci, value=h)
            cell.fill = hdr_fill
            cell.font = hdr_font
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = thin

        for ri, emp in enumerate(session.employees.all(), 2):
            row_data = [
                emp.employee_id, emp.name, emp.designation, emp.department, emp.band,
                float(emp.current_ctc),
                float(emp.manager_score) if emp.manager_score else '',
                float(emp.hod_score) if emp.hod_score else '',
                float(emp.management_score) if emp.management_score else '',
                emp.final_score,
                emp.effective_grade, emp.grade_config['label'],
                emp.effective_increment_pct, emp.increment_amount, emp.new_ctc,
                'Yes' if emp.promoted else 'No',
                'Yes' if emp.on_time_reward else 'No',
                'Yes' if emp.management_discretion else 'No',
                emp.notes,
            ]
            grade_hex = GRADE_COLORS.get(emp.effective_grade, 'f1f5f9')
            row_fill = PatternFill(start_color=grade_hex + '22', end_color=grade_hex + '22', fill_type='solid')
            for ci, val in enumerate(row_data, 1):
                cell = ws.cell(row=ri, column=ci, value=val)
                cell.border = thin
                cell.alignment = Alignment(vertical='center')
                if ci in (6, 14, 15):
                    cell.number_format = '#,##0.00'

        # ── Sheet 2: Summary ───────────────────────────────────────────────────
        ws2 = wb.create_sheet('Summary')
        summary = session_summary(session)
        s_fill = PatternFill(start_color='1e293b', end_color='1e293b', fill_type='solid')
        s_font = Font(color='FFFFFF', bold=True, size=12)

        ws2['A1'] = f'PMS Summary — {session.name} ({session.fiscal_year})'
        ws2['A1'].fill = s_fill
        ws2['A1'].font = s_font
        ws2.merge_cells('A1:D1')

        metrics = [
            ('Total Employees', summary['total_employees']),
            ('Total Current Payroll (Annual)', f"₹{summary['total_current_ctc']:,.0f}"),
            ('Total New Payroll (Annual)', f"₹{summary['total_new_ctc']:,.0f}"),
            ('Total Increment Cost', f"₹{summary['total_increment']:,.0f}"),
            ('Average Increment %', f"{summary['increment_pct']}%"),
            ('Employees Promoted', summary['promoted_count']),
            ('On-Time Rewards', summary['reward_count']),
        ]
        for ri, (label, val) in enumerate(metrics, 3):
            ws2.cell(row=ri, column=1, value=label).font = Font(bold=True)
            ws2.cell(row=ri, column=2, value=val)

        ws2.cell(row=12, column=1, value='Grade Distribution').font = Font(bold=True, size=11)
        for ri, (grade, count) in enumerate(sorted(summary['grade_distribution'].items()), 13):
            ws2.cell(row=ri, column=1, value=f'Grade {grade}')
            ws2.cell(row=ri, column=2, value=count)

        # col widths
        col_w = [14, 22, 18, 16, 8, 14, 10, 10, 12, 12, 8, 20, 12, 14, 14, 10, 14, 16, 30]
        for ci, w in enumerate(col_w, 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = w
        ws.row_dimensions[1].height = 36
        ws2.column_dimensions['A'].width = 30
        ws2.column_dimensions['B'].width = 22

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        response = HttpResponse(
            buf.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="PMS_Results_{session.fiscal_year}.xlsx"'
        return response
