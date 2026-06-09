"""
Employee-facing API views.
Employees access their own data by providing their employee_id.
"""
import json
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone

from ..models import EmployeeProfile, PerformanceCycle, GoalCard, Goal, KPI, QuarterlyReview, ApprovalLog
from ..serializers import (
    EmployeeProfileSerializer, PerformanceCycleSerializer,
    GoalCardSerializer, GoalSerializer, QuarterlyReviewSerializer
)
from ..notifications import notify_manager_on_employee_submit


class EmployeeProfileView(APIView):
    """GET /api/performance/employee/<employee_id>/ — fetch own profile."""

    def get(self, request, employee_id):
        source = getattr(request, 'app_source', 'performance')
        try:
            emp = EmployeeProfile.objects.get(employee_id=employee_id, is_active=True, app_source=source)
            serializer = EmployeeProfileSerializer(emp)
            return Response(serializer.data)
        except EmployeeProfile.DoesNotExist:
            return Response({'error': 'Employee not found'}, status=status.HTTP_404_NOT_FOUND)


class ActiveCyclesView(APIView):
    """GET /api/performance/cycles/active/ — get all open cycles.
    Auto-creates the annual cycle for the current Indian FY if none exists.
    """

    def get(self, request):
        source = getattr(request, 'app_source', 'performance')
        cycles = PerformanceCycle.objects.filter(
            status__in=['goal_setting', 'goals_locked', 'review_open'],
            app_source=source,
        )

        if not cycles.exists() and not PerformanceCycle.objects.filter(app_source=source).exists():
            from datetime import date
            cycle, _ = PerformanceCycle.objects.get_or_create(
                quarter=4,
                fiscal_year='2025-26',
                app_source=source,
                defaults={
                    'name': 'Annual Appraisal FY 2025-26',
                    'goal_setting_deadline': date(2027, 12, 31),
                    'review_start_date': date(2027, 4, 1),
                    'review_deadline': date(2027, 12, 31),
                    'status': 'goal_setting',
                    'created_by': 'System',
                }
            )
            cycles = PerformanceCycle.objects.filter(id=cycle.id)

        return Response(PerformanceCycleSerializer(cycles, many=True).data)


class EmployeeGoalCardView(APIView):
    """
    GET  /api/performance/goal-cards/<employee_id>/<cycle_id>/
    POST /api/performance/goal-cards/<employee_id>/<cycle_id>/
    — Create or retrieve the employee's GoalCard for a given cycle.
    """

    def get(self, request, employee_id, cycle_id):
        source = getattr(request, 'app_source', 'performance')
        try:
            emp = EmployeeProfile.objects.get(employee_id=employee_id, app_source=source)
            gc = GoalCard.objects.get(employee=emp, cycle_id=cycle_id)
            return Response(GoalCardSerializer(gc).data)
        except EmployeeProfile.DoesNotExist:
            return Response({'error': 'Employee not found'}, status=404)
        except GoalCard.DoesNotExist:
            return Response({'error': 'Goal card not found'}, status=404)

    def post(self, request, employee_id, cycle_id):
        """Create a new GoalCard with goals."""
        source = getattr(request, 'app_source', 'performance')
        try:
            emp = EmployeeProfile.objects.get(employee_id=employee_id, app_source=source)
            cycle = PerformanceCycle.objects.get(id=cycle_id, app_source=source)
        except EmployeeProfile.DoesNotExist:
            return Response({'error': 'Employee not found'}, status=404)
        except PerformanceCycle.DoesNotExist:
            return Response({'error': 'Cycle not found'}, status=404)

        # Enforce cycle phase: goals can only be set/edited during goal_setting
        if cycle.status != 'goal_setting':
            phase_messages = {
                'draft':        'This cycle has not been opened for goal setting yet. Contact HR.',
                'goals_locked': 'Goal setting is now locked for this cycle. Contact HR if you need to make changes.',
                'review_open':  'Goals are locked. The review phase is now open — submit your quarterly review instead.',
                'closed':       'This performance cycle is closed.',
            }
            msg = phase_messages.get(cycle.status, f'Goal setting is not allowed in the current phase: {cycle.get_status_display()}')
            return Response({'error': msg, 'cycle_status': cycle.status}, status=status.HTTP_403_FORBIDDEN)

        # Get or create the goal card
        gc, created = GoalCard.objects.get_or_create(employee=emp, cycle=cycle)

        # Save/overwrite KRAs and KPIs
        goals_data = request.data.get('goals', [])
        if goals_data:
            gc.goals.all().delete()
            for i, g in enumerate(goals_data):
                kra = Goal.objects.create(
                    goal_card=gc,
                    category=g.get('category', ''),
                    title=g.get('title', ''),
                    description=g.get('description', ''),
                    order=i
                )
                for j, kpi_data in enumerate(g.get('kpis', [])):
                    KPI.objects.create(
                        kra=kra,
                        metric=kpi_data.get('metric', ''),
                        target_value=kpi_data.get('target_value', ''),
                        weightage=kpi_data.get('weightage') or 0,
                        frequency=kpi_data.get('frequency', ''),
                        unit_of_measurement=kpi_data.get('unit_of_measurement', ''),
                        parameter_type=kpi_data.get('parameter_type', ''),
                        data_source=kpi_data.get('data_source', ''),
                        actual_achievement=kpi_data.get('actual_achievement', ''),
                        manager_score=kpi_data.get('manager_score') or None,
                        order=j
                    )

        # Save appraisal form steps 2-4 data
        gc.self_review_answers = request.data.get('self_review_answers', gc.self_review_answers)
        gc.key_skills = request.data.get('key_skills', gc.key_skills)
        gc.training_programs = request.data.get('training_programs', gc.training_programs)
        gc.feedback_manager = request.data.get('feedback_manager', gc.feedback_manager)
        gc.feedback_manager_rating = request.data.get('feedback_manager_rating', gc.feedback_manager_rating)
        gc.feedback_organization = request.data.get('feedback_organization', gc.feedback_organization)
        gc.feedback_organization_rating = request.data.get('feedback_organization_rating', gc.feedback_organization_rating)
        gc.save()

        return Response(GoalCardSerializer(gc).data, status=201 if created else 200)


class SubmitGoalCardView(APIView):
    """PATCH /api/performance/goal-cards/<gc_id>/submit/ — employee submits for manager review."""

    def patch(self, request, gc_id):
        try:
            gc = GoalCard.objects.get(id=gc_id)
        except GoalCard.DoesNotExist:
            return Response({'error': 'Goal card not found'}, status=404)

        # Enforce cycle phase: goals can only be submitted during goal_setting
        if gc.cycle.status != 'goal_setting':
            phase_messages = {
                'draft':        'This cycle is not open for goal setting yet.',
                'goals_locked': 'Goal setting is locked. You cannot submit goals at this time.',
                'review_open':  'Goals are locked. Submit your quarterly review instead.',
                'closed':       'This performance cycle is closed.',
            }
            msg = phase_messages.get(gc.cycle.status, f'Goal submission is not allowed: {gc.cycle.get_status_display()}')
            return Response({'error': msg, 'cycle_status': gc.cycle.status}, status=status.HTTP_403_FORBIDDEN)

        if gc.status not in ['draft', 'manager_rejected']:
            return Response({'error': f'Cannot submit from status: {gc.status}'}, status=400)

        gc.status = 'submitted'
        gc.submitted_at = timezone.now()
        gc.save()

        ApprovalLog.objects.create(
            goal_card=gc,
            actor_role='employee',
            actor_name=gc.employee.name,
            action='submitted',
            comment=request.data.get('comment', 'Submitted for manager review.')
        )

        notify_manager_on_employee_submit(gc)

        return Response(GoalCardSerializer(gc).data)


class EmployeeAllGoalCardsView(APIView):
    """GET /api/performance/employee/<employee_id>/goal-cards/ — all cycles for employee."""

    def get(self, request, employee_id):
        source = getattr(request, 'app_source', 'performance')
        try:
            emp = EmployeeProfile.objects.get(employee_id=employee_id, app_source=source)
            gcs = GoalCard.objects.filter(employee=emp).select_related('cycle')
            return Response(GoalCardSerializer(gcs, many=True).data)
        except EmployeeProfile.DoesNotExist:
            return Response({'error': 'Employee not found'}, status=404)


class SubmitQuarterlyReviewView(APIView):
    """POST /api/performance/reviews/<gc_id>/ — employee submits quarter-end review + evidence."""

    def post(self, request, gc_id):
        try:
            gc = GoalCard.objects.get(id=gc_id)
        except GoalCard.DoesNotExist:
            return Response({'error': 'Goal card not found'}, status=404)

        # Enforce cycle phase: reviews can only be submitted during review_open
        if gc.cycle.status != 'review_open':
            phase_messages = {
                'draft':        'The review phase has not started yet.',
                'goal_setting': 'Goal setting is still in progress. Reviews open after goals are locked.',
                'goals_locked': 'Goals are locked but the review window is not open yet. Wait for HR to open reviews.',
                'closed':       'This performance cycle is closed.',
            }
            msg = phase_messages.get(gc.cycle.status, f'Quarterly review submission is not allowed: {gc.cycle.get_status_display()}')
            return Response({'error': msg, 'cycle_status': gc.cycle.status}, status=status.HTTP_403_FORBIDDEN)

        # Enforce review deadline
        if gc.cycle.review_deadline and timezone.now().date() > gc.cycle.review_deadline:
            return Response({
                'error': f'The review submission deadline ({gc.cycle.review_deadline}) has passed.',
                'cycle_status': gc.cycle.status,
            }, status=status.HTTP_403_FORBIDDEN)

        review, created = QuarterlyReview.objects.get_or_create(goal_card=gc)

        review.employee_summary = request.data.get('employee_summary', '')
        review.key_achievements = request.data.get('key_achievements', '')
        review.challenges_faced = request.data.get('challenges_faced', '')
        review.support_required = request.data.get('support_required', '')
        review.training_needs = request.data.get('training_needs', '')
        review.career_aspirations = request.data.get('career_aspirations', '')
        review.learning_outcomes = request.data.get('learning_outcomes', '')
        review.next_quarter_plans = request.data.get('next_quarter_plans', '')
        review.overall_self_rating = request.data.get('overall_self_rating')
        review.status = 'submitted'
        review.submitted_at = timezone.now()

        # Handle evidence file
        if 'evidence_file' in request.FILES:
            review.evidence_file = request.FILES['evidence_file']

        review.save()

        # Update self-ratings per KPI
        kpi_ratings_raw = request.data.get('kpi_ratings', [])
        kpi_ratings = json.loads(kpi_ratings_raw) if isinstance(kpi_ratings_raw, str) else kpi_ratings_raw
        for kr in kpi_ratings:
            try:
                kpi = KPI.objects.get(id=kr['kpi_id'], kra__goal_card=gc)
                kpi.self_rating = kr.get('self_rating')
                kpi.self_completion_pct = kr.get('self_completion_pct')
                kpi.self_comments = kr.get('self_comments', '')
                kpi.achievement_description = kr.get('achievement_description', '')
                kpi.save()
            except KPI.DoesNotExist:
                pass

        ApprovalLog.objects.create(
            goal_card=gc,
            actor_role='employee',
            actor_name=gc.employee.name,
            action='review_submitted',
            comment='Quarterly review submitted for manager rating.'
        )

        return Response(QuarterlyReviewSerializer(review).data, status=201 if created else 200)
