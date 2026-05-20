"""
Progress tracking and report views.
Handles mid-quarter updates, employee progress reports, team dashboards, and org analytics.
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from ..models import (
    EmployeeProfile, PerformanceCycle, GoalCard,
    Goal, GoalProgressUpdate, QuarterlyReview,
)
from ..serializers import GoalProgressUpdateSerializer


class GoalProgressView(APIView):
    """GET/POST progress updates for a single goal."""

    def get(self, request, goal_id):
        try:
            goal = Goal.objects.get(id=goal_id)
        except Goal.DoesNotExist:
            return Response({'error': 'Goal not found'}, status=404)
        updates = goal.progress_updates.all()
        return Response(GoalProgressUpdateSerializer(updates, many=True).data)

    def post(self, request, goal_id):
        try:
            goal = Goal.objects.get(id=goal_id)
        except Goal.DoesNotExist:
            return Response({'error': 'Goal not found'}, status=404)

        data = request.data.copy()
        data['goal'] = goal_id
        serializer = GoalProgressUpdateSerializer(data=data)
        if serializer.is_valid():
            update = serializer.save()
            # Keep goal's self_completion_pct in sync with latest update
            goal.self_completion_pct = update.completion_pct
            goal.save(update_fields=['self_completion_pct'])
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)


class EmployeeProgressReportView(APIView):
    """Full performance history for one employee — all cycles, goals, updates, scores."""

    def get(self, request, employee_id):
        try:
            emp = EmployeeProfile.objects.get(employee_id=employee_id)
        except EmployeeProfile.DoesNotExist:
            return Response({'error': 'Employee not found'}, status=404)

        goal_cards = (
            GoalCard.objects
            .filter(employee=emp)
            .select_related('cycle')
            .prefetch_related('goals', 'goals__progress_updates')
            .order_by('cycle__fiscal_year', 'cycle__quarter')
        )

        cycles_data = []
        score_trend = []   # [{quarter, score}] for the line chart

        for gc in goal_cards:
            # Try review data
            review_payload = None
            try:
                r = gc.review
                review_payload = {
                    'performance_band': r.performance_band,
                    'performance_band_display': r.get_performance_band_display(),
                    'final_weighted_score': float(r.final_weighted_score) if r.final_weighted_score else None,
                    'status': r.status,
                    'status_display': r.get_status_display(),
                    'manager_overall_rating': r.manager_overall_rating,
                    'hr_final_rating': r.hr_final_rating,
                    'employee_summary': r.employee_summary,
                    'key_achievements': r.key_achievements,
                    'submitted_at': r.submitted_at,
                }
                if r.final_weighted_score:
                    score_trend.append({
                        'label': gc.cycle.name,
                        'quarter': gc.cycle.quarter,
                        'fiscal_year': gc.cycle.fiscal_year,
                        'score': float(r.final_weighted_score),
                        'band': r.performance_band,
                    })
            except Exception:
                pass

            goals_data = []
            for goal in gc.goals.all():
                updates = goal.progress_updates.all()
                latest = updates.first()
                goals_data.append({
                    'id': goal.id,
                    'title': goal.title,
                    'category': goal.category,
                    'category_display': goal.get_category_display(),
                    'weightage': goal.weightage,
                    'kpi_metric': goal.kpi_metric,
                    'target_value': goal.target_value,
                    'self_completion_pct': goal.self_completion_pct or 0,
                    'self_rating': goal.self_rating,
                    'manager_rating': goal.manager_rating,
                    'hr_rating': goal.hr_rating,
                    'final_score': float(goal.final_score) if goal.final_score else None,
                    'current_status': latest.status if latest else 'not_started',
                    'current_status_display': latest.get_status_display() if latest else 'Not Started',
                    'progress_updates': [
                        {
                            'id': u.id,
                            'completion_pct': u.completion_pct,
                            'status': u.status,
                            'status_display': u.get_status_display(),
                            'notes': u.notes,
                            'highlights': u.highlights,
                            'blockers': u.blockers,
                            'update_date': u.update_date,
                        }
                        for u in updates
                    ],
                })

            cycles_data.append({
                'cycle_id': gc.cycle.id,
                'cycle_name': gc.cycle.name,
                'quarter': gc.cycle.quarter,
                'fiscal_year': gc.cycle.fiscal_year,
                'cycle_status': gc.cycle.status,
                'goal_card_id': gc.id,
                'goal_card_status': gc.status,
                'goal_card_status_display': gc.get_status_display(),
                'total_weightage': gc.total_weightage,
                'final_weighted_score': gc.final_weighted_score,
                'goals': goals_data,
                'review': review_payload,
            })

        return Response({
            'employee': {
                'employee_id': emp.employee_id,
                'name': emp.name,
                'designation': emp.designation,
                'department': emp.department,
                'zone': emp.zone,
                'subzone': emp.subzone,
                'reporting_manager_id': emp.reporting_manager_id,
                'joined_date': emp.joined_date,
            },
            'cycles': cycles_data,
            'score_trend': score_trend,
            'total_cycles': len(cycles_data),
        })


class TeamProgressReportView(APIView):
    """Manager's team progress dashboard for a given cycle."""

    def get(self, request, manager_id):
        try:
            manager = EmployeeProfile.objects.get(employee_id=manager_id)
        except EmployeeProfile.DoesNotExist:
            return Response({'error': 'Manager not found'}, status=404)

        cycle_id = request.query_params.get('cycle_id')
        team = EmployeeProfile.objects.filter(reporting_manager_id=manager_id, is_active=True)

        team_data = []
        for member in team:
            m = {
                'employee_id': member.employee_id,
                'name': member.name,
                'designation': member.designation,
                'zone': member.zone,
                'subzone': member.subzone,
                'goal_card': None,
            }

            if cycle_id:
                try:
                    gc = GoalCard.objects.prefetch_related(
                        'goals', 'goals__progress_updates'
                    ).get(employee=member, cycle_id=cycle_id)

                    goal_summaries = []
                    total_completion = 0
                    at_risk = False

                    for goal in gc.goals.all():
                        latest = goal.progress_updates.first()
                        pct = goal.self_completion_pct or 0
                        s = latest.status if latest else 'not_started'
                        if s in ('at_risk', 'blocked'):
                            at_risk = True
                        total_completion += pct
                        goal_summaries.append({
                            'id': goal.id,
                            'title': goal.title,
                            'category': goal.category,
                            'completion_pct': pct,
                            'status': s,
                            'status_display': latest.get_status_display() if latest else 'Not Started',
                            'final_score': float(goal.final_score) if goal.final_score else None,
                        })

                    total_goals = len(goal_summaries)
                    avg_completion = round(total_completion / total_goals, 1) if total_goals else 0

                    m['goal_card'] = {
                        'id': gc.id,
                        'status': gc.status,
                        'status_display': gc.get_status_display(),
                        'total_goals': total_goals,
                        'avg_completion': avg_completion,
                        'at_risk': at_risk,
                        'final_weighted_score': gc.final_weighted_score,
                        'goals': goal_summaries,
                    }
                except GoalCard.DoesNotExist:
                    pass

            team_data.append(m)

        # Stats
        total = team.count()
        submitted = sum(
            1 for m in team_data
            if m['goal_card'] and m['goal_card']['status'] in (
                'submitted', 'manager_approved', 'hr_approved', 'finalized'
            )
        )
        not_started = sum(1 for m in team_data if not m['goal_card'])
        at_risk = sum(1 for m in team_data if m['goal_card'] and m['goal_card'].get('at_risk'))
        on_track = total - not_started - at_risk

        return Response({
            'manager': {
                'employee_id': manager.employee_id,
                'name': manager.name,
                'designation': manager.designation,
            },
            'stats': {
                'team_size': total,
                'submitted': submitted,
                'not_started': not_started,
                'at_risk': at_risk,
                'on_track': max(on_track, 0),
            },
            'team': team_data,
        })


class OrgAnalyticsView(APIView):
    """HR org-wide analytics for a cycle — feeds all charts on the HR dashboard."""

    def get(self, request):
        cycle_id = request.query_params.get('cycle_id')
        if not cycle_id:
            return Response({'error': 'cycle_id query param is required'}, status=400)

        try:
            cycle = PerformanceCycle.objects.get(id=cycle_id)
        except PerformanceCycle.DoesNotExist:
            return Response({'error': 'Cycle not found'}, status=404)

        goal_cards = GoalCard.objects.filter(cycle=cycle).select_related('employee')
        all_goals = Goal.objects.filter(goal_card__cycle=cycle)
        reviews = QuarterlyReview.objects.filter(
            goal_card__cycle=cycle,
            status__in=('hr_finalized', 'published'),
        ).select_related('goal_card__employee')

        # --- Summary ---
        total_gc = goal_cards.count()
        submitted_gc = goal_cards.filter(
            status__in=('submitted', 'manager_approved', 'hr_approved', 'finalized')
        ).count()
        finalized_gc = goal_cards.filter(status='finalized').count()
        published_rev = reviews.filter(status='published').count()

        # --- Band distribution ---
        band_map = {
            'outstanding': '🏆 Outstanding',
            'exceeds': '⭐ Exceeds',
            'meets': '✅ Meets',
            'below': '⚠️ Below',
            'poor': '❌ Needs Improvement',
        }
        band_dist = []
        for code, label in band_map.items():
            count = reviews.filter(performance_band=code).count()
            band_dist.append({'band': code, 'label': label, 'count': count})

        # --- Department performance ---
        dept_agg: dict = {}
        for gc in goal_cards:
            dept = gc.employee.department or 'Unassigned'
            if dept not in dept_agg:
                dept_agg[dept] = {'total_score': 0, 'scored': 0, 'total': 0}
            dept_agg[dept]['total'] += 1
            score = gc.final_weighted_score
            if score:
                dept_agg[dept]['total_score'] += score
                dept_agg[dept]['scored'] += 1

        dept_chart = sorted([
            {
                'name': dept,
                'avg_score': round(d['total_score'] / d['scored'], 2) if d['scored'] else 0,
                'count': d['total'],
            }
            for dept, d in dept_agg.items()
        ], key=lambda x: x['avg_score'], reverse=True)

        # --- Zone performance ---
        zone_agg: dict = {}
        for gc in goal_cards:
            zone = gc.employee.zone or 'Unassigned'
            if zone not in zone_agg:
                zone_agg[zone] = {'total_score': 0, 'scored': 0, 'total': 0}
            zone_agg[zone]['total'] += 1
            score = gc.final_weighted_score
            if score:
                zone_agg[zone]['total_score'] += score
                zone_agg[zone]['scored'] += 1

        zone_chart = sorted([
            {
                'name': zone,
                'avg_score': round(d['total_score'] / d['scored'], 2) if d['scored'] else 0,
                'count': d['total'],
            }
            for zone, d in zone_agg.items()
        ], key=lambda x: x['avg_score'], reverse=True)

        # --- Goal category breakdown ---
        cat_agg: dict = {}
        for goal in all_goals:
            cat = goal.category
            if cat not in cat_agg:
                cat_agg[cat] = {
                    'category_display': goal.get_category_display(),
                    'count': 0, 'total_completion': 0, 'scored': 0, 'total_score': 0,
                }
            cat_agg[cat]['count'] += 1
            cat_agg[cat]['total_completion'] += goal.self_completion_pct or 0
            if goal.final_score:
                cat_agg[cat]['scored'] += 1
                cat_agg[cat]['total_score'] += float(goal.final_score)

        category_chart = [
            {
                'category': cat,
                'label': d['category_display'],
                'count': d['count'],
                'avg_completion': round(d['total_completion'] / d['count'], 1) if d['count'] else 0,
                'avg_score': round(d['total_score'] / d['scored'], 2) if d['scored'] else 0,
            }
            for cat, d in cat_agg.items()
        ]

        # --- Top performers ---
        top = reviews.filter(
            status='published', final_weighted_score__isnull=False
        ).order_by('-final_weighted_score')[:10]

        top_performers = [
            {
                'rank': i + 1,
                'employee_id': r.goal_card.employee.employee_id,
                'name': r.goal_card.employee.name,
                'designation': r.goal_card.employee.designation,
                'department': r.goal_card.employee.department,
                'zone': r.goal_card.employee.zone,
                'score': float(r.final_weighted_score),
                'band': r.performance_band,
                'band_display': r.get_performance_band_display(),
            }
            for i, r in enumerate(top)
        ]

        return Response({
            'cycle': {
                'id': cycle.id,
                'name': cycle.name,
                'quarter': cycle.quarter,
                'fiscal_year': cycle.fiscal_year,
                'status': cycle.status,
                'status_display': cycle.get_status_display(),
            },
            'summary': {
                'total_goal_cards': total_gc,
                'submitted_goal_cards': submitted_gc,
                'finalized_goal_cards': finalized_gc,
                'published_reviews': published_rev,
                'submission_rate': round(submitted_gc / total_gc * 100, 1) if total_gc else 0,
            },
            'band_distribution': band_dist,
            'department_performance': dept_chart,
            'zone_performance': zone_chart,
            'category_analytics': category_chart,
            'top_performers': top_performers,
        })
