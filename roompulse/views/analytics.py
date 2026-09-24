"""Utilisation analytics — Admin/Super Admin only.

Gives Super Admin the "look over the whole project" view: booking volume,
approval funnel, busiest rooms/departments/hours, turnaround time, item
requests and support tickets.

Everything here is measured over a window (`?days=`, default 30). Alongside
it, `totals` carries all-time counts, so a caller can tell "nothing has ever
happened" from "nothing happened lately" — a page of zeroes that cannot tell
those apart reads as a broken dashboard.
"""
from datetime import timedelta
from django.db.models import Count, Avg, F, ExpressionWrapper, DurationField
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response

from ..models import (Room, BookingRequest, ResourceRequest, Employee, AdminUser,
                      SupportTicket)
from .perms import require_role


class AnalyticsView(APIView):
    def get(self, request):
        if (err := require_role(request, 'admin', 'super_admin')):
            return err

        try:
            days = max(1, min(365, int(request.query_params.get('days', 30))))
        except (TypeError, ValueError):
            days = 30
        since = timezone.localdate() - timedelta(days=days)

        qs = BookingRequest.objects.filter(date__gte=since)
        total = qs.count()
        by_status = {row['status']: row['n'] for row in
                    qs.values('status').annotate(n=Count('id'))}

        by_room = list(qs.exclude(status='cancelled').values('room__name', 'room__label')
                        .annotate(n=Count('id')).order_by('-n')[:10])
        by_dept = list(qs.exclude(department='').exclude(status='cancelled')
                        .values('department').annotate(n=Count('id')).order_by('-n')[:10])
        by_purpose = list(qs.exclude(status='cancelled').values('purpose')
                          .annotate(n=Count('id')).order_by('-n'))

        # Busiest hour of day (by start_time hour) among approved bookings.
        approved = qs.filter(status='approved')
        hour_counts = {}
        for b in approved.only('start_time'):
            hour_counts[b.start_time.hour] = hour_counts.get(b.start_time.hour, 0) + 1
        busiest_hour = max(hour_counts.items(), key=lambda kv: kv[1])[0] if hour_counts else None

        # Approval turnaround: created_at -> reviewed_at, for requests that
        # went through actual review (skips auto-approved admin bookings,
        # which would otherwise make turnaround look artificially instant).
        reviewed = qs.filter(status__in=('approved', 'rejected'),
                             reviewed_at__isnull=False).exclude(reviewed_by=F('requested_by_email'))
        turnaround = reviewed.annotate(
            delta=ExpressionWrapper(F('reviewed_at') - F('created_at'), output_field=DurationField())
        ).aggregate(avg=Avg('delta'))['avg']

        approval_rate = None
        decided = by_status.get('approved', 0) + by_status.get('rejected', 0)
        if decided:
            approval_rate = round(by_status.get('approved', 0) / decided * 100, 1)

        # ── resource (non-room) requests over the same window ──
        rqs = ResourceRequest.objects.filter(created_at__date__gte=since)
        r_total = rqs.count()
        r_by_status = {row['status']: row['n'] for row in
                      rqs.values('status').annotate(n=Count('id'))}
        r_by_category = list(rqs.exclude(status='cancelled').values('category')
                             .annotate(n=Count('id')).order_by('-n'))

        # ── support tickets over the same window ──
        # The helpdesk is the busiest thing in AdminPulse and used to be
        # missing from this page entirely, so a super admin looking at the
        # whole project saw everything except the part people actually use.
        # Only what people actually raised. Work the team logged itself is
        # real work, but it is not demand -- counting it here would say the
        # helpdesk got busier every time IT wrote up a job nobody asked for.
        tqs = SupportTicket.objects.filter(created_at__date__gte=since).exclude(origin='logged')
        t_logged = SupportTicket.objects.filter(
            origin='logged', performed_on__gte=since).count()
        t_total = tqs.count()
        t_by_status = {row['status']: row['n'] for row in
                       tqs.values('status').annotate(n=Count('id'))}
        t_by_category = list(tqs.exclude(status='cancelled').values('category')
                             .annotate(n=Count('id')).order_by('-n'))
        t_by_priority = list(tqs.exclude(status='cancelled').values('priority')
                             .annotate(n=Count('id')).order_by('-n'))
        t_resolved = tqs.filter(status='closed', reviewed_at__isnull=False).annotate(
            delta=ExpressionWrapper(F('reviewed_at') - F('created_at'), output_field=DurationField())
        ).aggregate(avg=Avg('delta'))['avg']
        t_open = sum(t_by_status.get(k, 0) for k in ('pending', 'approved', 'in_progress'))

        return Response({
            'period_days': days,
            'since': since.isoformat(),
            'total_bookings': total,
            'by_status': by_status,
            'approval_rate_pct': approval_rate,
            'avg_turnaround_minutes': round(turnaround.total_seconds() / 60, 1) if turnaround else None,
            'busiest_hour': busiest_hour,
            'top_rooms': [{'room': f"{r['room__label']} {r['room__name']}".strip(), 'bookings': r['n']}
                         for r in by_room],
            'top_departments': [{'department': r['department'], 'bookings': r['n']} for r in by_dept],
            'by_purpose': by_purpose,
            'resource_requests': {
                'total': r_total,
                'by_status': r_by_status,
                'by_category': r_by_category,
                'pending': r_by_status.get('pending', 0),
            },
            'tickets': {
                'total': t_total,
                'logged_directly': t_logged,
                'by_status': t_by_status,
                'by_category': t_by_category,
                'by_priority': t_by_priority,
                'open': t_open,
                'pending': t_by_status.get('pending', 0),
                'avg_resolution_minutes': (round(t_resolved.total_seconds() / 60, 1)
                                           if t_resolved else None),
            },
            # All-time counts, so a window with nothing in it can say whether
            # there is nothing at all or only nothing *recently* -- a page of
            # zeroes otherwise reads as a broken dashboard.
            'totals': {
                'rooms': Room.objects.filter(is_active=True).count(),
                'employees': Employee.objects.count(),
                'admins': AdminUser.objects.count(),
                'bookings': BookingRequest.objects.count(),
                'resource_requests': ResourceRequest.objects.count(),
                'tickets': SupportTicket.objects.count(),
            },
        })
