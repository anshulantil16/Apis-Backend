"""TA/DA Portal API — OTP auth, request creation (3 types), workflow, policy validation."""
import io
import json
import secrets
from datetime import timedelta, datetime, date

import openpyxl
from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.utils import timezone

ADMIN_BOOTSTRAP_EMAIL = 'anshul@apisindia.com'
_ADMIN_OTP_KEY = 'tada_admin_otp'
_ADMIN_OTP_TTL = 300
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from .models import TadaUser, TadaOTP, TravelRequest, ExpenseItem, LocalTravelItem, ApprovalLog
from . import policy


# ── helpers ───────────────────────────────────────────────────────────────────
def _mask_email(email):
    if not email or '@' not in email:
        return '***@***.***'
    local, domain = email.split('@', 1)
    ml = local[0] + '*' * max(1, len(local) - 2) + local[-1] if len(local) > 2 else '*' * len(local)
    return f"{ml}@{domain}"


def _parse_date(v):
    if not v:
        return None
    if isinstance(v, (datetime,)):
        return v.date()
    if isinstance(v, date):
        return v
    for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except Exception:
            pass
    return None


def _sf(v, d=0):
    if v is None or str(v).strip() == '':
        return d
    try:
        return float(str(v).replace(',', ''))
    except Exception:
        return d


def serialize_user(u):
    return {
        'id': u.id, 'employee_id': u.employee_id, 'name': u.name, 'email': u.email,
        'designation': u.designation, 'department': u.department, 'level': u.level,
        'hq_city': u.hq_city, 'role': u.role, 'reporting_manager_id': u.reporting_manager_id,
        'caps': policy.get_caps(u.level),
    }


def serialize_request(r, detail=False):
    d = {
        'id': r.id, 'type': r.request_type, 'type_label': r.get_request_type_display(),
        'status': r.status, 'status_label': r.get_status_display(),
        'employee_id': r.user.employee_id, 'employee_name': r.user.name,
        'level': r.user.level, 'department': r.user.department,
        'purpose': r.purpose, 'from_date': str(r.from_date) if r.from_date else None,
        'to_date': str(r.to_date) if r.to_date else None, 'number_of_days': r.number_of_days,
        'travel_address': r.travel_address, 'destination_city': r.destination_city,
        'city_grade': r.city_grade, 'contact_number': r.contact_number,
        'sanction_number': r.sanction_number, 'estimate_amount': float(r.estimate_amount),
        'travel_mode': r.travel_mode, 'local_travel_type': r.local_travel_type,
        'travel_mode_date': str(r.travel_mode_date) if r.travel_mode_date else None,
        'travel_mode_time_pref': r.travel_mode_time_pref,
        'travel_mode_time_pref_label': r.get_travel_mode_time_pref_display() if r.travel_mode_time_pref else None,
        'return_mode_date': str(r.return_mode_date) if r.return_mode_date else None,
        'return_mode_time_pref': r.return_mode_time_pref,
        'return_mode_time_pref_label': r.get_return_mode_time_pref_display() if r.return_mode_time_pref else None,
        'total_claimed': float(r.total_claimed), 'total_approved': float(r.total_approved),
        'manager_remarks': r.manager_remarks, 'hr_remarks': r.hr_remarks, 'finance_remarks': r.finance_remarks,
        'created_at': r.created_at.strftime('%Y-%m-%d %H:%M'),
        'submitted_at': r.submitted_at.strftime('%Y-%m-%d %H:%M') if r.submitted_at else None,
    }
    if detail:
        d['expense_items'] = [{
            'id': i.id, 'category': i.category, 'category_label': i.get_category_display(),
            'date': str(i.date) if i.date else None, 'description': i.description,
            'from_location': i.from_location, 'to_location': i.to_location, 'mode': i.mode,
            'km': float(i.km), 'claimed_amount': float(i.claimed_amount),
            'approved_amount': float(i.approved_amount) if i.approved_amount is not None else None,
            'has_bill': bool(i.bill), 'bill_url': f'/api/tada/bill/{i.id}/' if i.bill else None,
            'gst_verified': i.gst_verified,
            'policy_cap': float(i.policy_cap) if i.policy_cap is not None else None,
            'policy_flag': i.policy_flag,
        } for i in r.expense_items.all()]
        d['local_items'] = [{
            'id': i.id, 'date': str(i.date) if i.date else None, 'purpose': i.purpose,
            'from_location': i.from_location, 'to_location': i.to_location, 'mode': i.mode,
            'km': float(i.km), 'amount': float(i.amount), 'policy_flag': i.policy_flag,
        } for i in r.local_items.all()]
        d['logs'] = [{
            'stage': l.stage, 'action': l.action, 'by_name': l.by_name,
            'remarks': l.remarks, 'timestamp': l.timestamp.strftime('%Y-%m-%d %H:%M'),
        } for l in r.logs.all()]
    return d


def _get_user(request):
    """Identify the acting user by employee_id passed from the SPA (internal tool)."""
    eid = (request.data.get('employee_id') or request.query_params.get('employee_id') or '').strip()
    if not eid:
        return None
    return TadaUser.objects.filter(employee_id=eid, is_active=True).first()


# ── AUTH (OTP) ────────────────────────────────────────────────────────────────
class SendOTPView(APIView):
    def post(self, request):
        eid = (request.data.get('employee_id') or '').strip()
        if not eid:
            return Response({'error': 'Employee ID is required.'}, status=400)
        u = TadaUser.objects.filter(employee_id=eid, is_active=True).first()
        if not u:
            return Response({'error': 'Employee ID not found in TA/DA portal.'}, status=404)
        if not u.email:
            return Response({'error': 'No email on file. Contact HR/IT.'}, status=400)
        TadaOTP.objects.filter(user=u, is_used=False).delete()
        code = f"{secrets.randbelow(1_000_000):06d}"
        TadaOTP.objects.create(user=u, code=code, expires_at=timezone.now() + timedelta(minutes=5))
        try:
            send_mail(
                subject='Your APIS TA/DA Portal Login OTP',
                message=(f"Hi {u.name},\n\nYour OTP for the APIS TA/DA Portal is: {code}\n"
                         f"Valid for 5 minutes. Do not share it.\n\n— APIS Team"),
                from_email=settings.DEFAULT_FROM_EMAIL, recipient_list=[u.email], fail_silently=False,
            )
        except Exception as e:
            return Response({'error': f'Could not send OTP email: {e}'}, status=500)
        return Response({'message': 'OTP sent.', 'masked_email': _mask_email(u.email)})


class VerifyOTPView(APIView):
    def post(self, request):
        eid = (request.data.get('employee_id') or '').strip()
        code = (request.data.get('otp') or '').strip()
        u = TadaUser.objects.filter(employee_id=eid, is_active=True).first()
        if not u:
            return Response({'error': 'Invalid employee.'}, status=404)
        otp = TadaOTP.objects.filter(user=u, code=code, is_used=False).order_by('-created_at').first()
        if not otp:
            return Response({'error': 'Invalid OTP.'}, status=400)
        if otp.expires_at < timezone.now():
            return Response({'error': 'OTP expired. Please request a new one.'}, status=400)
        otp.is_used = True
        otp.save(update_fields=['is_used'])
        return Response({'message': 'Login successful.', 'user': serialize_user(u)})


class AdminOTPView(APIView):
    """Send an OTP to the hardcoded admin email — works before any users are imported."""
    def post(self, request):
        code = f"{secrets.randbelow(1_000_000):06d}"
        cache.set(_ADMIN_OTP_KEY, code, timeout=_ADMIN_OTP_TTL)
        try:
            send_mail(
                subject='APIS TA/DA Portal — Admin Access OTP',
                message=f"Admin login OTP for the APIS TA/DA Portal:\n\n  {code}\n\nValid for 5 minutes.\n\n— APIS System",
                from_email=settings.DEFAULT_FROM_EMAIL, recipient_list=[ADMIN_BOOTSTRAP_EMAIL], fail_silently=False,
            )
        except Exception as e:
            cache.delete(_ADMIN_OTP_KEY)
            return Response({'error': f'Failed to send email: {e}'}, status=500)
        return Response({'message': 'OTP sent to admin email.', 'masked_email': _mask_email(ADMIN_BOOTSTRAP_EMAIL)})


class AdminVerifyView(APIView):
    """Verify the admin OTP and return a synthetic admin session."""
    def post(self, request):
        code = (request.data.get('otp') or '').strip()
        stored = cache.get(_ADMIN_OTP_KEY)
        if stored is None:
            return Response({'error': 'OTP expired or not requested.'}, status=400)
        if stored != code:
            return Response({'error': 'Invalid OTP.'}, status=400)
        cache.delete(_ADMIN_OTP_KEY)
        return Response({'message': 'Admin login successful.', 'user': {
            'id': 0, 'employee_id': 'ADMIN', 'name': 'System Admin', 'role': 'admin',
            'level': '', 'designation': 'Administrator', 'department': 'IT', 'hq_city': '', 'caps': {},
        }})


# ── USER DIRECTORY (import / template / list) ─────────────────────────────────
class UserTemplateView(APIView):
    def get(self, request):
        from django.http import HttpResponse
        from openpyxl.styles import PatternFill, Font, Alignment
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'TADA Users'
        headers = ['Employee ID *', 'Name *', 'Email *', 'Designation', 'Department',
                   'Level (M1-M7/E1-E4) *', 'HQ City', 'Reporting Manager ID',
                   'Role (employee/manager/hr/finance)', 'Vehicle RC No']
        hf = PatternFill(start_color='2E75B6', end_color='2E75B6', fill_type='solid')
        for ci, h in enumerate(headers, 1):
            c = ws.cell(row=1, column=ci, value=h)
            c.fill = hf; c.font = Font(color='FFFFFF', bold=True)
            c.alignment = Alignment(horizontal='center', wrap_text=True)
        samples = [
            ['E1001', 'Rahul Verma', 'rahul@apisindia.com', 'Area Sales Manager', 'Sales', 'M1', 'Delhi', 'M2001', 'employee', 'DL01AB1234'],
            ['M2001', 'Suresh Rao', 'suresh@apisindia.com', 'Regional Sales Manager', 'Sales', 'M4', 'Mumbai', 'M5001', 'manager', ''],
            ['H3001', 'Neha HR', 'neha@apisindia.com', 'HR Manager', 'People & Culture', 'M3', 'Delhi', '', 'hr', ''],
            ['F4001', 'Amit Finance', 'amit@apisindia.com', 'Finance Manager', 'Finance', 'M4', 'Delhi', '', 'finance', ''],
        ]
        for ri, row in enumerate(samples, 2):
            for ci, v in enumerate(row, 1):
                ws.cell(row=ri, column=ci, value=v)
        for i in range(1, len(headers) + 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = 22
        buf = io.BytesIO(); wb.save(buf); buf.seek(0)
        resp = HttpResponse(buf.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = 'attachment; filename="TADA_Users_Template.xlsx"'
        return resp


class UserImportView(APIView):
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        f = request.FILES.get('file')
        if not f:
            return Response({'error': 'No file provided.'}, status=400)
        try:
            wb = openpyxl.load_workbook(f, data_only=True); ws = wb.active
        except Exception as e:
            return Response({'error': f'Cannot read file: {e}'}, status=400)
        ALIASES = {
            'employee id': 'employee_id', 'name': 'name', 'email': 'email',
            'designation': 'designation', 'department': 'department',
            'level': 'level', 'level (m1-m7/e1-e4)': 'level',
            'hq city': 'hq_city', 'reporting manager id': 'reporting_manager_id',
            'role': 'role', 'role (employee/manager/hr/finance)': 'role',
            'vehicle rc no': 'vehicle_rc_no',
        }
        header = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
        cmap = {}
        for ci, cell in enumerate(header):
            if cell is None:
                continue
            key = str(cell).strip().lower().replace('*', '').strip()
            if key in ALIASES:
                cmap[ALIASES[key]] = ci
        created = updated = 0
        errors = []
        for ri, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not any(row):
                continue
            data = {k: (row[ci] if ci < len(row) else None) for k, ci in cmap.items()}
            eid = str(data.get('employee_id') or '').strip()
            name = str(data.get('name') or '').strip()
            if not eid or not name:
                errors.append(f'Row {ri}: missing Employee ID or Name'); continue
            role = str(data.get('role') or 'employee').strip().lower()
            if role not in ('employee', 'manager', 'hr', 'finance', 'admin'):
                role = 'employee'
            _, was_created = TadaUser.objects.update_or_create(
                employee_id=eid,
                defaults={
                    'name': name, 'email': str(data.get('email') or '').strip(),
                    'designation': str(data.get('designation') or '').strip(),
                    'department': str(data.get('department') or '').strip(),
                    'level': str(data.get('level') or '').strip().upper(),
                    'hq_city': str(data.get('hq_city') or '').strip(),
                    'reporting_manager_id': str(data.get('reporting_manager_id') or '').strip(),
                    'role': role, 'vehicle_rc_no': str(data.get('vehicle_rc_no') or '').strip(),
                    'is_active': True,
                },
            )
            created += was_created; updated += not was_created
        return Response({'message': f'✅ Imported {created} new, {updated} updated.',
                         'created': created, 'updated': updated, 'errors': errors,
                         'total': TadaUser.objects.count()})


class UsersListView(APIView):
    def get(self, request):
        users = TadaUser.objects.all()
        return Response({'users': [serialize_user(u) for u in users], 'total': users.count()})

    def delete(self, request):
        TadaUser.objects.all().delete()
        return Response({'message': 'All TA/DA users cleared.'})


class AdminOverviewView(APIView):
    """Super-admin oversight: all requests + stats across every stage."""
    def get(self, request):
        rs = list(TravelRequest.objects.select_related('user').all())
        by_status, by_type, by_dept = {}, {}, {}
        for r in rs:
            by_status[r.status] = by_status.get(r.status, 0) + 1
            by_type[r.request_type] = by_type.get(r.request_type, 0) + 1
            d = r.user.department or 'Unknown'
            by_dept[d] = by_dept.get(d, 0) + 1
        users = TadaUser.objects.all()
        by_role = {}
        for u in users:
            by_role[u.role] = by_role.get(u.role, 0) + 1
        rejected = sum(1 for r in rs if r.status in ('manager_rejected', 'hr_rejected', 'finance_rejected'))
        return Response({
            'total_requests': len(rs),
            'total_users': users.count(),
            'total_claimed': round(sum(float(r.total_claimed) for r in rs), 2),
            'total_approved': round(sum(float(r.total_approved) for r in rs), 2),
            'pending_manager': by_status.get('submitted', 0),
            'pending_hr': by_status.get('manager_approved', 0),
            'pending_finance': by_status.get('hr_approved', 0),
            'approved': by_status.get('finance_approved', 0),
            'paid': by_status.get('paid', 0),
            'rejected': rejected,
            'by_status': by_status,
            'by_type': by_type,
            'by_department': dict(sorted(by_dept.items(), key=lambda x: -x[1])),
            'users_by_role': by_role,
            'requests': [serialize_request(r) for r in rs[:1000]],
        })


class AdminResetView(APIView):
    """Clear TA/DA data. what='requests' (default) or 'all' (also users)."""
    def post(self, request):
        what = (request.data.get('what') or 'requests').strip()
        n = TravelRequest.objects.count()
        TravelRequest.objects.all().delete()   # cascades expense/local items + logs
        msg = f'Cleared {n} requests.'
        if what == 'all':
            un = TadaUser.objects.count()
            TadaUser.objects.all().delete()
            msg += f' Cleared {un} users.'
        return Response({'message': msg})


class CapsView(APIView):
    def get(self, request):
        level = request.query_params.get('level', '')
        city = request.query_params.get('city', '')
        out = policy.get_caps(level)
        if city:
            out['city_grade'] = policy.city_grade(city)
        return Response(out)


# ── EMPLOYEE: create requests ─────────────────────────────────────────────────
class MyRequestsView(APIView):
    def get(self, request):
        u = _get_user(request)
        if not u:
            return Response({'error': 'Login required.'}, status=401)
        rs = TravelRequest.objects.filter(user=u)
        return Response({'requests': [serialize_request(r) for r in rs]})


class RequestDetailView(APIView):
    def get(self, request, req_id):
        r = TravelRequest.objects.filter(id=req_id).first()
        if not r:
            return Response({'error': 'Not found'}, status=404)
        return Response(serialize_request(r, detail=True))


class CreateTourSanctionView(APIView):
    def post(self, request):
        u = _get_user(request)
        if not u:
            return Response({'error': 'Login required.'}, status=401)
        d = request.data
        r = TravelRequest.objects.create(
            user=u, request_type='tour_sanction', status='submitted',
            purpose=d.get('purpose', ''), travel_address=d.get('travel_address', ''),
            destination_city=d.get('destination_city', ''),
            city_grade=policy.city_grade(d.get('destination_city', '')),
            from_date=_parse_date(d.get('from_date')), to_date=_parse_date(d.get('to_date')),
            contact_number=d.get('contact_number', ''), sanction_number=d.get('sanction_number', ''),
            estimate_amount=_sf(d.get('estimate_amount')), travel_mode=d.get('travel_mode', ''),
            travel_mode_date=_parse_date(d.get('travel_mode_date')),
            travel_mode_time_pref=d.get('travel_mode_time_pref', ''),
            return_mode_date=_parse_date(d.get('return_mode_date')),
            return_mode_time_pref=d.get('return_mode_time_pref', ''),
            submitted_at=timezone.now(),
        )
        ApprovalLog.objects.create(request=r, stage='employee', action='submitted', by_name=u.name)
        return Response({'message': 'Tour Programme submitted for Manager approval.',
                         'request': serialize_request(r, detail=True)})


class CreateTravelExpenseView(APIView):
    """Multipart: 'payload' = JSON (request + items[]), files bill_<idx> per item."""
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def post(self, request):
        u = _get_user(request)
        if not u:
            return Response({'error': 'Login required.'}, status=401)
        try:
            payload = json.loads(request.data.get('payload') or '{}')
        except Exception:
            payload = request.data
        items = payload.get('items', [])
        city = payload.get('destination_city', '')
        cgrade = policy.city_grade(city)

        # Hard-stop: 60-day deadline (checked against the earliest item / from_date)
        fd = _parse_date(payload.get('from_date'))
        if fd and not policy.is_within_deadline(fd):
            return Response({'error': f'Blocked: travel date is beyond the {policy.SUBMISSION_DEADLINE_DAYS}-day '
                                      f'submission deadline.'}, status=400)

        r = TravelRequest.objects.create(
            user=u, request_type='travel_expense', status='submitted',
            purpose=payload.get('purpose', ''), destination_city=city, city_grade=cgrade,
            from_date=fd, to_date=_parse_date(payload.get('to_date')),
            sanction_number=payload.get('sanction_number', ''), travel_mode=payload.get('travel_mode', ''),
            submitted_at=timezone.now(),
        )
        total = 0.0
        for idx, it in enumerate(items):
            claimed = _sf(it.get('claimed_amount'))
            bill = request.FILES.get(f'bill_{idx}')
            cap, flags = policy.validate_expense_item(
                u.level, it.get('category', 'misc'), cgrade, claimed, bool(bill),
                mode=it.get('mode', ''), km=_sf(it.get('km')), date_val=_parse_date(it.get('date')))
            item = ExpenseItem.objects.create(
                request=r, category=it.get('category', 'misc'), date=_parse_date(it.get('date')),
                description=it.get('description', ''), from_location=it.get('from_location', ''),
                to_location=it.get('to_location', ''), mode=it.get('mode', ''), km=_sf(it.get('km')),
                claimed_amount=claimed, gst_verified=bool(it.get('gst_verified')),
                policy_cap=cap, policy_flag=' | '.join(flags),
            )
            if bill:
                item.bill.save(f'bill_{r.id}_{idx}_{bill.name}', bill, save=True)
            total += claimed
        r.total_claimed = total
        r.save(update_fields=['total_claimed'])
        ApprovalLog.objects.create(request=r, stage='employee', action='submitted', by_name=u.name)
        return Response({'message': 'Travelling Expenses submitted for Manager approval.',
                         'request': serialize_request(r, detail=True)})


class CreateLocalTravelView(APIView):
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def post(self, request):
        u = _get_user(request)
        if not u:
            return Response({'error': 'Login required.'}, status=401)
        try:
            payload = json.loads(request.data.get('payload') or '{}')
        except Exception:
            payload = request.data
        rows = payload.get('items', [])
        r = TravelRequest.objects.create(
            user=u, request_type='local_travel', status='submitted',
            purpose=payload.get('purpose', ''), local_travel_type=payload.get('local_travel_type', ''),
            from_date=_parse_date(payload.get('from_date')), to_date=_parse_date(payload.get('to_date')),
            submitted_at=timezone.now(),
        )
        band = policy.band_for_level(u.level)
        daily_cap = policy.LOCAL_CONVEYANCE.get(band, {}).get('daily')
        total = 0.0
        for it in rows:
            amt = _sf(it.get('amount'))
            flags = []
            if daily_cap is not None and amt > daily_cap:
                flags.append(f'Exceeds daily local conveyance cap ₹{daily_cap}')
            LocalTravelItem.objects.create(
                request=r, date=_parse_date(it.get('date')), purpose=it.get('purpose', ''),
                from_location=it.get('from_location', ''), to_location=it.get('to_location', ''),
                mode=it.get('mode', ''), km=_sf(it.get('km')), amount=amt, policy_flag=' | '.join(flags))
            total += amt
        r.total_claimed = total
        r.save(update_fields=['total_claimed'])
        ApprovalLog.objects.create(request=r, stage='employee', action='submitted', by_name=u.name)
        return Response({'message': 'Local Travel submitted for Manager approval.',
                         'request': serialize_request(r, detail=True)})


class BillDownloadView(APIView):
    def get(self, request, item_id):
        from django.http import FileResponse
        it = ExpenseItem.objects.filter(id=item_id).first()
        if not it or not it.bill:
            return Response({'error': 'No bill'}, status=404)
        return FileResponse(it.bill.open('rb'), filename=f'bill_{item_id}.pdf')


# ── APPROVALS (Manager / HR / Finance) ────────────────────────────────────────
_STAGE_FLOW = {
    'manager': {'from': 'submitted',        'approve': 'manager_approved', 'reject': 'manager_rejected'},
    'hr':      {'from': 'manager_approved', 'approve': 'hr_approved',      'reject': 'hr_rejected'},
    'finance': {'from': 'hr_approved',      'approve': 'finance_approved', 'reject': 'finance_rejected'},
}


class PendingQueueView(APIView):
    """Role-based queue of requests awaiting the acting user's action."""
    def get(self, request):
        u = _get_user(request)
        if not u:
            return Response({'error': 'Login required.'}, status=401)
        role = u.role
        if role == 'manager':
            # requests submitted by this manager's direct reports
            report_ids = list(TadaUser.objects.filter(reporting_manager_id=u.employee_id)
                              .values_list('employee_id', flat=True))
            rs = TravelRequest.objects.filter(user__employee_id__in=report_ids, status='submitted')
            others = TravelRequest.objects.filter(user__employee_id__in=report_ids).exclude(status='submitted')
        elif role == 'hr':
            rs = TravelRequest.objects.filter(status='manager_approved')
            others = TravelRequest.objects.filter(status__in=['hr_approved', 'hr_rejected', 'finance_approved', 'finance_rejected', 'paid'])
        elif role == 'finance':
            rs = TravelRequest.objects.filter(status='hr_approved')
            others = TravelRequest.objects.filter(status__in=['finance_approved', 'finance_rejected', 'paid'])
        else:
            return Response({'pending': [], 'processed': []})
        return Response({'pending': [serialize_request(r) for r in rs],
                         'processed': [serialize_request(r) for r in others[:100]]})


class ActionView(APIView):
    """Approve / reject a request at the acting user's stage."""
    def post(self, request, req_id):
        u = _get_user(request)
        if not u:
            return Response({'error': 'Login required.'}, status=401)
        r = TravelRequest.objects.filter(id=req_id).first()
        if not r:
            return Response({'error': 'Not found'}, status=404)
        action = (request.data.get('action') or '').strip().lower()   # approve / reject / paid
        remarks = request.data.get('remarks', '')
        flow = _STAGE_FLOW.get(u.role)
        if not flow:
            return Response({'error': 'Your role cannot action requests.'}, status=403)

        if action == 'paid' and u.role == 'finance' and r.status == 'finance_approved':
            r.status = 'paid'
            r.finance_action_at = timezone.now()
            r.save()
            ApprovalLog.objects.create(request=r, stage='finance', action='paid', by_name=u.name, remarks=remarks)
            return Response({'message': 'Marked as Paid.', 'request': serialize_request(r, detail=True)})

        if r.status != flow['from']:
            return Response({'error': f'Request is not awaiting your action (status: {r.get_status_display()}).'}, status=400)
        if action not in ('approve', 'reject'):
            return Response({'error': 'action must be approve or reject.'}, status=400)

        new_status = flow['approve'] if action == 'approve' else flow['reject']
        r.status = new_status
        now = timezone.now()
        if u.role == 'manager':
            r.manager_remarks = remarks; r.manager_action_at = now
        elif u.role == 'hr':
            r.hr_remarks = remarks; r.hr_action_at = now
        elif u.role == 'finance':
            r.finance_remarks = remarks; r.finance_action_at = now
            if action == 'approve':
                r.total_approved = r.total_claimed
        r.save()
        ApprovalLog.objects.create(request=r, stage=u.role, action=action + 'd', by_name=u.name, remarks=remarks)
        return Response({'message': f'Request {action}d.', 'request': serialize_request(r, detail=True)})
