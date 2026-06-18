"""
Employee-facing API views.
"""
import json
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser
from django.utils import timezone
from django.db import transaction

from appraisal.models import EmployeeProfile, PerformanceCycle, GoalCard, Goal, KPI, QuarterlyReview, ApprovalLog
from appraisal.serializers import (
    EmployeeProfileSerializer, PerformanceCycleSerializer,
    GoalCardSerializer, GoalSerializer, QuarterlyReviewSerializer
)
from ..notifications import notify_manager_on_employee_submit


class EmployeeProfileView(APIView):
    def get(self, request, employee_id):
        try:
            emp = EmployeeProfile.objects.get(employee_id=employee_id, is_active=True)
            return Response(EmployeeProfileSerializer(emp).data)
        except EmployeeProfile.DoesNotExist:
            return Response({'error': 'Employee not found'}, status=404)


class ActiveCyclesView(APIView):
    def get(self, request):
        cycles = PerformanceCycle.objects.filter(
            status__in=['goal_setting', 'goals_locked', 'review_open']
        )
        if not cycles.exists() and not PerformanceCycle.objects.exists():
            from datetime import date
            cycle, _ = PerformanceCycle.objects.get_or_create(
                quarter=4, fiscal_year='2025-26',
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
    def get(self, request, employee_id, cycle_id):
        try:
            emp = EmployeeProfile.objects.get(employee_id=employee_id)
            gc = GoalCard.objects.get(employee=emp, cycle_id=cycle_id)
            return Response(GoalCardSerializer(gc, context={'request': request}).data)
        except EmployeeProfile.DoesNotExist:
            return Response({'error': 'Employee not found'}, status=404)
        except GoalCard.DoesNotExist:
            return Response({'error': 'Goal card not found'}, status=404)

    def post(self, request, employee_id, cycle_id):
        try:
            emp = EmployeeProfile.objects.get(employee_id=employee_id)
            cycle = PerformanceCycle.objects.get(id=cycle_id)
        except EmployeeProfile.DoesNotExist:
            return Response({'error': 'Employee not found'}, status=404)
        except PerformanceCycle.DoesNotExist:
            return Response({'error': 'Cycle not found'}, status=404)

        # Allow saving if cycle is goal_setting, OR if employee has an existing draft/rejected card
        existing_gc = GoalCard.objects.filter(employee=emp, cycle=cycle).first()
        if cycle.status != 'goal_setting':
            if not existing_gc or existing_gc.status not in ('draft', 'manager_rejected'):
                phase_messages = {
                    'draft':        'This cycle has not been opened for goal setting yet. Contact HR.',
                    'goals_locked': 'Goal setting is now locked. Contact HR if you need changes.',
                    'review_open':  'Goals are locked. The review phase is now open.',
                    'closed':       'This performance cycle is closed.',
                }
                msg = phase_messages.get(cycle.status, f'Goal setting not allowed: {cycle.get_status_display()}')
                return Response({'error': msg, 'cycle_status': cycle.status}, status=403)

        gc, created = GoalCard.objects.get_or_create(employee=emp, cycle=cycle)

        try:
            with transaction.atomic():
                goals_data = request.data.get('goals', None)
                if goals_data is not None and len(goals_data) > 0:
                    incoming_goal_ids = [g.get('id') for g in goals_data if g.get('id')]
                    gc.goals.exclude(id__in=incoming_goal_ids).delete()

                    for i, g in enumerate(goals_data):
                        goal_id = g.get('id')
                        kra = Goal.objects.filter(id=goal_id, goal_card=gc).first() if goal_id else None
                        if kra:
                            kra.category = g.get('category', kra.category)
                            kra.title = g.get('title', kra.title)
                            kra.description = g.get('description', kra.description)
                            kra.order = i
                            kra.save()
                        else:
                            kra = Goal.objects.create(
                                goal_card=gc,
                                category=g.get('category', ''),
                                title=g.get('title', ''),
                                description=g.get('description', ''),
                                order=i
                            )

                        incoming_kpi_ids = [k.get('id') for k in g.get('kpis', []) if k.get('id')]
                        kra.kpis.exclude(id__in=incoming_kpi_ids).delete()

                        for j, kd in enumerate(g.get('kpis', [])):
                            kpi = KPI.objects.filter(id=kd.get('id'), kra=kra).first() if kd.get('id') else None
                            if kpi:
                                kpi.metric = kd.get('metric', kpi.metric)
                                kpi.target_value = kd.get('target_value', kpi.target_value)
                                if 'weightage' in kd and kd['weightage'] != '' and kd['weightage'] is not None:
                                    try:
                                        kpi.weightage = float(kd['weightage'])
                                    except (ValueError, TypeError):
                                        pass
                                kpi.frequency = kd.get('frequency', kpi.frequency)
                                kpi.unit_of_measurement = kd.get('unit_of_measurement', kpi.unit_of_measurement)
                                kpi.parameter_type = kd.get('parameter_type', kpi.parameter_type)
                                kpi.data_source = kd.get('data_source', kpi.data_source)
                                kpi.actual_achievement = kd.get('actual_achievement', kpi.actual_achievement)
                                kpi.order = j
                                kpi.save()
                            else:
                                try:
                                    weightage = float(kd.get('weightage') or 0)
                                except (ValueError, TypeError):
                                    weightage = 0
                                KPI.objects.create(
                                    kra=kra,
                                    metric=kd.get('metric', ''),
                                    target_value=kd.get('target_value', ''),
                                    weightage=weightage,
                                    frequency=kd.get('frequency', ''),
                                    unit_of_measurement=kd.get('unit_of_measurement', ''),
                                    parameter_type=kd.get('parameter_type', ''),
                                    data_source=kd.get('data_source', ''),
                                    actual_achievement=kd.get('actual_achievement', ''),
                                    manager_score=kd.get('manager_score') or None,
                                    order=j
                                )

                # Save steps 2-4 data
                req = request.data
                if 'self_review_answers' in req:
                    gc.self_review_answers = req['self_review_answers']
                if 'key_skills' in req:
                    gc.key_skills = req['key_skills']
                if 'training_programs' in req:
                    gc.training_programs = req['training_programs']
                if 'feedback_manager' in req:
                    gc.feedback_manager = req['feedback_manager']
                if 'feedback_manager_rating' in req:
                    gc.feedback_manager_rating = req['feedback_manager_rating'] or None
                if 'feedback_organization' in req:
                    gc.feedback_organization = req['feedback_organization']
                if 'feedback_organization_rating' in req:
                    gc.feedback_organization_rating = req['feedback_organization_rating'] or None
                gc.save()

        except Exception as e:
            import traceback
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"GoalCard save error for {employee_id}: {str(e)}\n{traceback.format_exc()}")
            return Response({'error': f'Save failed: {str(e)}'}, status=500)

        return Response(GoalCardSerializer(gc, context={'request': request}).data, status=201 if created else 200)


class SubmitGoalCardView(APIView):
    def patch(self, request, gc_id):
        try:
            gc = GoalCard.objects.get(id=gc_id)
        except GoalCard.DoesNotExist:
            return Response({'error': 'Goal card not found'}, status=404)

        if gc.cycle.status != 'goal_setting':
            phase_messages = {
                'draft':        'This cycle is not open for goal setting yet.',
                'goals_locked': 'Goal setting is locked. You cannot submit goals at this time.',
                'review_open':  'Goals are locked. Submit your quarterly review instead.',
                'closed':       'This performance cycle is closed.',
            }
            msg = phase_messages.get(gc.cycle.status, f'Goal submission not allowed: {gc.cycle.get_status_display()}')
            return Response({'error': msg, 'cycle_status': gc.cycle.status}, status=403)

        if gc.status not in ['draft', 'manager_rejected']:
            return Response({'error': f'Cannot submit from status: {gc.status}'}, status=400)

        gc.status = 'submitted'
        gc.submitted_at = timezone.now()
        gc.save()

        ApprovalLog.objects.create(
            goal_card=gc, actor_role='employee', actor_name=gc.employee.name,
            action='submitted', comment=request.data.get('comment', 'Submitted for manager review.')
        )
        notify_manager_on_employee_submit(gc)
        return Response(GoalCardSerializer(gc, context={'request': request}).data)


class EmployeeAllGoalCardsView(APIView):
    def get(self, request, employee_id):
        try:
            emp = EmployeeProfile.objects.get(employee_id=employee_id)
            gcs = GoalCard.objects.filter(employee=emp).select_related('cycle')
            return Response(GoalCardSerializer(gcs, many=True, context={'request': request}).data)
        except EmployeeProfile.DoesNotExist:
            return Response({'error': 'Employee not found'}, status=404)


class EmployeeSupportDocumentUploadView(APIView):
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request, gc_id):
        import os
        from django.conf import settings
        try:
            gc = GoalCard.objects.get(id=gc_id)
        except GoalCard.DoesNotExist:
            return Response({'error': 'Goal card not found'}, status=404)

        if gc.status not in ('draft', 'submitted'):
            return Response({'error': 'Cannot upload document at this stage.'}, status=400)

        file = request.FILES.get('document')
        if not file:
            return Response({'error': 'No file provided.'}, status=400)

        if file.size > 10 * 1024 * 1024:
            return Response({'error': 'File too large. Maximum size is 10MB.'}, status=400)

        upload_dir = os.path.join(settings.MEDIA_ROOT, 'appraisal_docs')
        os.makedirs(upload_dir, exist_ok=True)

        if gc.support_document:
            gc.support_document.delete(save=False)

        gc.support_document = file
        gc.support_document_name = file.name
        gc.save()
        return Response(GoalCardSerializer(gc, context={'request': request}).data)

    def delete(self, request, gc_id):
        try:
            gc = GoalCard.objects.get(id=gc_id)
        except GoalCard.DoesNotExist:
            return Response({'error': 'Goal card not found'}, status=404)

        if gc.support_document:
            gc.support_document.delete(save=False)
            gc.support_document = None
            gc.support_document_name = ''
            gc.save()
        return Response({'message': 'Document removed.'})


class SubmitQuarterlyReviewView(APIView):
    def post(self, request, gc_id):
        try:
            gc = GoalCard.objects.get(id=gc_id)
        except GoalCard.DoesNotExist:
            return Response({'error': 'Goal card not found'}, status=404)

        if gc.cycle.status != 'review_open':
            phase_messages = {
                'draft':        'The review phase has not started yet.',
                'goal_setting': 'Goal setting is still in progress.',
                'goals_locked': 'Review window not open yet. Wait for HR.',
                'closed':       'This performance cycle is closed.',
            }
            msg = phase_messages.get(gc.cycle.status, f'Review submission not allowed: {gc.cycle.get_status_display()}')
            return Response({'error': msg, 'cycle_status': gc.cycle.status}, status=403)

        if gc.cycle.review_deadline and timezone.now().date() > gc.cycle.review_deadline:
            return Response({'error': f'Review deadline ({gc.cycle.review_deadline}) has passed.'}, status=403)

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

        if 'evidence_file' in request.FILES:
            review.evidence_file = request.FILES['evidence_file']
        review.save()

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
            goal_card=gc, actor_role='employee', actor_name=gc.employee.name,
            action='review_submitted', comment='Quarterly review submitted for manager rating.'
        )
        return Response(QuarterlyReviewSerializer(review).data, status=201 if created else 200)
