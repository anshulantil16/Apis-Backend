"""
Employee-facing API views.
Employees access their own data by providing their employee_id.
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone

from ..models import EmployeeProfile, PerformanceCycle, GoalCard, Goal, QuarterlyReview, ApprovalLog
from ..serializers import (
    EmployeeProfileSerializer, PerformanceCycleSerializer,
    GoalCardSerializer, GoalSerializer, QuarterlyReviewSerializer
)


class EmployeeProfileView(APIView):
    """GET /api/performance/employee/<employee_id>/ — fetch own profile."""

    def get(self, request, employee_id):
        try:
            emp = EmployeeProfile.objects.get(employee_id=employee_id, is_active=True)
            serializer = EmployeeProfileSerializer(emp)
            return Response(serializer.data)
        except EmployeeProfile.DoesNotExist:
            return Response({'error': 'Employee not found'}, status=status.HTTP_404_NOT_FOUND)


class ActiveCyclesView(APIView):
    """GET /api/performance/cycles/active/ — get all open cycles."""

    def get(self, request):
        cycles = PerformanceCycle.objects.filter(
            status__in=['goal_setting', 'goals_locked', 'review_open']
        )
        return Response(PerformanceCycleSerializer(cycles, many=True).data)


class EmployeeGoalCardView(APIView):
    """
    GET  /api/performance/goal-cards/<employee_id>/<cycle_id>/
    POST /api/performance/goal-cards/<employee_id>/<cycle_id>/
    — Create or retrieve the employee's GoalCard for a given cycle.
    """

    def get(self, request, employee_id, cycle_id):
        try:
            emp = EmployeeProfile.objects.get(employee_id=employee_id)
            gc = GoalCard.objects.get(employee=emp, cycle_id=cycle_id)
            return Response(GoalCardSerializer(gc).data)
        except EmployeeProfile.DoesNotExist:
            return Response({'error': 'Employee not found'}, status=404)
        except GoalCard.DoesNotExist:
            return Response({'error': 'Goal card not found'}, status=404)

    def post(self, request, employee_id, cycle_id):
        """Create a new GoalCard with goals."""
        try:
            emp = EmployeeProfile.objects.get(employee_id=employee_id)
            cycle = PerformanceCycle.objects.get(id=cycle_id)
        except EmployeeProfile.DoesNotExist:
            return Response({'error': 'Employee not found'}, status=404)
        except PerformanceCycle.DoesNotExist:
            return Response({'error': 'Cycle not found'}, status=404)

        # Get or create the goal card
        gc, created = GoalCard.objects.get_or_create(employee=emp, cycle=cycle)

        # Save/overwrite goals
        goals_data = request.data.get('goals', [])
        if goals_data:
            gc.goals.all().delete()
            for i, g in enumerate(goals_data):
                Goal.objects.create(
                    goal_card=gc,
                    category=g.get('category', 'sales'),
                    title=g.get('title', ''),
                    description=g.get('description', ''),
                    kpi_metric=g.get('kpi_metric', ''),
                    target_value=g.get('target_value', ''),
                    weightage=g.get('weightage', 20),
                    order=i
                )

        return Response(GoalCardSerializer(gc).data, status=201 if created else 200)


class SubmitGoalCardView(APIView):
    """PATCH /api/performance/goal-cards/<gc_id>/submit/ — employee submits for manager review."""

    def patch(self, request, gc_id):
        try:
            gc = GoalCard.objects.get(id=gc_id)
        except GoalCard.DoesNotExist:
            return Response({'error': 'Goal card not found'}, status=404)

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

        return Response(GoalCardSerializer(gc).data)


class EmployeeAllGoalCardsView(APIView):
    """GET /api/performance/employee/<employee_id>/goal-cards/ — all cycles for employee."""

    def get(self, request, employee_id):
        try:
            emp = EmployeeProfile.objects.get(employee_id=employee_id)
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

        # Update self-ratings per goal
        goal_ratings = request.data.get('goal_ratings', [])
        for gr in goal_ratings:
            try:
                goal = Goal.objects.get(id=gr['goal_id'], goal_card=gc)
                goal.self_rating = gr.get('self_rating')
                goal.self_completion_pct = gr.get('self_completion_pct')
                goal.self_comments = gr.get('self_comments', '')
                goal.achievement_description = gr.get('achievement_description', '')
                goal.save()
            except Goal.DoesNotExist:
                pass

        ApprovalLog.objects.create(
            goal_card=gc,
            actor_role='employee',
            actor_name=gc.employee.name,
            action='review_submitted',
            comment='Quarterly review submitted for manager rating.'
        )

        return Response(QuarterlyReviewSerializer(review).data, status=201 if created else 200)
