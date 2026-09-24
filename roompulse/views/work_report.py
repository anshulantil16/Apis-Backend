"""What the IT and admin team actually did in a month.

Two things feed it and they have to be counted together, or the number is
wrong in a way nobody notices: tickets people raised and the team closed, and
jobs the team did that nobody raised a ticket for. The second kind is a large
part of the work -- a server restarted, a laptop rebuilt for a joiner, a
printer fixed because somebody walked over and asked -- and until it could be
logged, a monthly report measured how often people happened to use the ticket
form rather than how much work was done.

Both live in SupportTicket, separated by `origin`, so this is one query and
one set of categories. The split is reported rather than hidden: "sixty jobs,
of which thirty-eight came through the queue" says something about the team
AND something about whether people are using the queue.
"""
from calendar import monthrange
from datetime import date

from django.db.models import Count, Sum
from rest_framework.views import APIView
from rest_framework.response import Response

from ..models import AdminUser, SupportTicket
from .perms import require_role


def _month_bounds(raw, today):
    """-> (first, last, label) for a 'YYYY-MM' string, or this month."""
    year, month = today.year, today.month
    if raw:
        try:
            year, month = (int(p) for p in str(raw).split('-')[:2])
        except (TypeError, ValueError):
            pass
    if not (1 <= month <= 12 and 2000 <= year <= 2100):
        year, month = today.year, today.month
    first = date(year, month, 1)
    last = date(year, month, monthrange(year, month)[1])
    return first, last, f'{first:%B %Y}'


class WorkReportView(APIView):
    """GET ?month=YYYY-MM — the month's work, per person and per category."""

    def get(self, request):
        # The team's own record of its output. An employee sees their own
        # tickets elsewhere; this is not that.
        if (err := require_role(request, 'it_support', 'admin', 'super_admin')):
            return err

        from django.utils import timezone
        first, last, label = _month_bounds(request.query_params.get('month'),
                                           timezone.localdate())

        # Work that HAPPENED in the month, by the day it was done -- not by
        # the day the ticket was raised. A ticket opened in March and closed
        # in April is April's work, and counting it in March would credit a
        # month in which nothing was finished.
        done = SupportTicket.objects.filter(performed_on__range=(first, last))

        by_origin = {r['origin']: r['n'] for r in
                     done.values('origin').annotate(n=Count('id'))}
        minutes = done.aggregate(m=Sum('time_spent_minutes'))['m'] or 0

        # Per person. Names live on AdminUser for staff, so the report can say
        # "Ravi" rather than an email address.
        names = {a.email.lower(): a.name for a in AdminUser.objects.all()}
        people = {}
        for row in done.values('performed_by_email', 'origin').annotate(
                n=Count('id'), mins=Sum('time_spent_minutes')):
            email = (row['performed_by_email'] or '').lower()
            p = people.setdefault(email, {
                'email': email,
                'name': names.get(email) or (email.split('@')[0] if email else 'Unattributed'),
                'closed': 0, 'logged': 0, 'total': 0, 'minutes': 0,
            })
            key = 'logged' if row['origin'] == 'logged' else 'closed'
            p[key] += row['n']
            p['total'] += row['n']
            p['minutes'] += row['mins'] or 0

        by_category = [
            {'category': r['category'],
             'label': dict(SupportTicket.CATEGORY_CHOICES).get(r['category'], r['category']),
             'count': r['n']}
            for r in done.values('category').annotate(n=Count('id')).order_by('-n')
        ]

        # Still open at the end of the month: not output, but the other half
        # of the picture, and the number a manager asks about next.
        outstanding = SupportTicket.objects.filter(
            status__in=('pending', 'approved', 'in_progress')).count()

        return Response({
            'month': f'{first:%Y-%m}',
            'label': label,
            'from': first.isoformat(),
            'to': last.isoformat(),
            'total': sum(by_origin.values()),
            'from_tickets': by_origin.get('requested', 0),
            'logged_directly': by_origin.get('logged', 0),
            'minutes_recorded': minutes,
            'people': sorted(people.values(), key=lambda p: -p['total']),
            'by_category': by_category,
            'still_open': outstanding,
        })
