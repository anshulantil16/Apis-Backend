"""
Manager-facing API views.
Managers can see their direct reports' goal cards and provide ratings.
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone

from appraisal.models import EmployeeProfile, GoalCard, Goal, KPI, QuarterlyReview, ApprovalLog, CompetencyRating
from appraisal.serializers import GoalCardSerializer, QuarterlyReviewSerializer
from ..notifications import notify_on_manager_approval


class ManagerTeamView(APIView):
    """GET /api/performance/manager/<manager_id>/team/ — list direct reports with their goal cards."""

    def get(self, request, manager_id):
        try:
            EmployeeProfile.objects.get(employee_id=manager_id)
        except EmployeeProfile.DoesNotExist:
            return Response({'error': 'Manager not found'}, status=404)

        cycle_id = request.query_params.get('cycle_id')
        team = EmployeeProfile.objects.filter(reporting_manager_id=manager_id, is_active=True)

        result = []
        for emp in team:
            entry = {
                'employee_id': emp.employee_id,
                'name': emp.name,
                'designation': emp.designation,
                'zone': emp.zone,
                'goal_card': None
            }
            try:
                gc_qs = GoalCard.objects.filter(employee=emp)
                if cycle_id:
                    gc_qs = gc_qs.filter(cycle_id=cycle_id)
                gc = gc_qs.latest('created_at')
                entry['goal_card'] = GoalCardSerializer(gc, context={'request': request}).data
            except GoalCard.DoesNotExist:
                pass
            result.append(entry)

        return Response(result)


class ManagerReviewGoalCardView(APIView):
    """PATCH /api/performance/goal-cards/<gc_id>/manager-review/ — approve/reject/adjust goals."""

    def patch(self, request, gc_id):
        try:
            gc = GoalCard.objects.get(id=gc_id)
        except GoalCard.DoesNotExist:
            return Response({'error': 'Goal card not found'}, status=404)

        if gc.status != 'submitted':
            return Response({'error': f'Can only review submitted cards. Current: {gc.status}'}, status=400)

        action = request.data.get('action')  # 'approved' or 'rejected'
        if action not in ['approved', 'rejected']:
            return Response({'error': 'action must be approved or rejected'}, status=400)

        gc.status = 'manager_approved' if action == 'approved' else 'manager_rejected'
        gc.manager_remarks = request.data.get('remarks', '')
        gc.manager_suggested_skills = request.data.get('manager_suggested_skills', gc.manager_suggested_skills)
        gc.manager_special_achievements = request.data.get('manager_special_achievements', gc.manager_special_achievements)
        gc.manager_promoted = request.data.get('manager_promoted', gc.manager_promoted)
        gc.manager_promoted_justification = request.data.get('manager_promoted_justification', gc.manager_promoted_justification)
        gc.manager_salary_correction = request.data.get('manager_salary_correction', gc.manager_salary_correction)
        gc.manager_salary_justification = request.data.get('manager_salary_justification', gc.manager_salary_justification)
        gc.manager_uplift_ratings = request.data.get('manager_uplift_ratings', gc.manager_uplift_ratings)
        gc.manager_uplift_comments = request.data.get('manager_uplift_comments', gc.manager_uplift_comments)
        gc.manager_reviewed_at = timezone.now()
        gc.save()

        # Apply goal-level adjustments if provided
        goal_adjustments = request.data.get('goal_adjustments', [])
        for adj in goal_adjustments:
            try:
                goal = Goal.objects.get(id=adj['goal_id'], goal_card=gc)
                if 'weightage' in adj:
                    goal.weightage = adj['weightage']
                if 'target_value' in adj:
                    goal.target_value = adj['target_value']
                if 'kpi_metric' in adj:
                    goal.kpi_metric = adj['kpi_metric']
                goal.save()
            except Goal.DoesNotExist:
                pass

        manager_name = request.data.get('manager_name', 'Manager')
        ApprovalLog.objects.create(
            goal_card=gc,
            actor_role='manager',
            actor_name=manager_name,
            action=action,
            comment=gc.manager_remarks
        )

        if action == 'approved':
            notify_on_manager_approval(gc, manager_name)

        return Response(GoalCardSerializer(gc, context={'request': request}).data)


class ManagerRateReviewView(APIView):
    """PATCH /api/performance/reviews/<review_id>/manager-rate/ — manager provides per-goal ratings."""

    def patch(self, request, review_id):
        try:
            review = QuarterlyReview.objects.get(id=review_id)
        except QuarterlyReview.DoesNotExist:
            return Response({'error': 'Review not found'}, status=404)

        review.manager_overall_rating = request.data.get('manager_overall_rating')
        review.manager_review_comments = request.data.get('manager_review_comments', '')
        review.employee_strengths = request.data.get('employee_strengths', '')
        review.areas_of_improvement = request.data.get('areas_of_improvement', '')
        review.development_plan = request.data.get('development_plan', '')
        review.promotion_recommendation = request.data.get('promotion_recommendation', '')
        review.increment_recommendation = request.data.get('increment_recommendation', '')
        review.status = 'manager_reviewed'
        review.manager_reviewed_at = timezone.now()
        review.save()

        # Save competency ratings (Section C)
        for cr in request.data.get('competency_ratings', []):
            CompetencyRating.objects.update_or_create(
                goal_card=review.goal_card,
                competency=cr['competency'],
                defaults={'marks': cr.get('marks'), 'manager_remarks': cr.get('manager_remarks', '')},
            )

        # Per-goal manager ratings
        goal_ratings = request.data.get('goal_ratings', [])
        for gr in goal_ratings:
            try:
                goal = Goal.objects.get(id=gr['goal_id'], goal_card=review.goal_card)
                goal.manager_rating = gr.get('manager_rating')
                goal.manager_comments = gr.get('manager_comments', '')
                goal.final_score = goal.compute_final_score()
                goal.save()
            except Goal.DoesNotExist:
                pass

        ApprovalLog.objects.create(
            goal_card=review.goal_card,
            actor_role='manager',
            actor_name=request.data.get('manager_name', 'Manager'),
            action='rated',
            comment=review.manager_review_comments
        )

        return Response(QuarterlyReviewSerializer(review).data)


class ManagerKPIScoresView(APIView):
    """PATCH /api/performance/goal-cards/<gc_id>/manager-kpi-scores/ — save per-KPI manager scores and comments."""

    def patch(self, request, gc_id):
        try:
            gc = GoalCard.objects.get(id=gc_id)
        except GoalCard.DoesNotExist:
            return Response({'error': 'Goal card not found'}, status=404)

        kpi_scores = request.data.get('kpi_scores', [])
        for ks in kpi_scores:
            try:
                kpi = KPI.objects.get(id=ks['kpi_id'], kra__goal_card=gc)
                if 'manager_score' in ks:
                    kpi.manager_score = ks['manager_score'] if ks['manager_score'] != '' else None
                if 'manager_comments' in ks:
                    kpi.manager_comments = ks['manager_comments']
                kpi.save()
            except KPI.DoesNotExist:
                pass

        return Response(GoalCardSerializer(gc, context={'request': request}).data)


class ManagerPendingReviewsView(APIView):
    """GET /api/performance/manager/<manager_id>/pending-reviews/ — submitted reviews awaiting manager rating."""

    def get(self, request, manager_id):
        team = EmployeeProfile.objects.filter(reporting_manager_id=manager_id, is_active=True)
        emp_ids = team.values_list('id', flat=True)

        reviews = QuarterlyReview.objects.filter(
            goal_card__employee_id__in=emp_ids,
            status='submitted'
        ).select_related('goal_card__employee', 'goal_card__cycle')

        return Response(QuarterlyReviewSerializer(reviews, many=True).data)
