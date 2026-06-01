"""
HOD-facing API views.
HODs see all manager-approved goal cards for employees under them and submit their own remarks.
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from django.utils import timezone

from ..models import EmployeeProfile, GoalCard, ApprovalLog
from ..serializers import GoalCardSerializer


class HODTeamView(APIView):
    """GET /api/performance/hod/<hod_id>/team/?cycle_id=<id> — all employees under this HOD."""

    def get(self, request, hod_id):
        try:
            EmployeeProfile.objects.get(employee_id=hod_id)
        except EmployeeProfile.DoesNotExist:
            return Response({'error': 'HOD not found'}, status=404)

        cycle_id = request.query_params.get('cycle_id')
        team = EmployeeProfile.objects.filter(hod_id=hod_id, is_active=True)

        result = []
        for emp in team:
            entry = {
                'employee_id': emp.employee_id,
                'name': emp.name,
                'designation': emp.designation,
                'zone': emp.zone,
                'department': emp.department,
                'goal_card': None,
            }
            try:
                gc_qs = GoalCard.objects.filter(employee=emp)
                if cycle_id:
                    gc_qs = gc_qs.filter(cycle_id=cycle_id)
                gc = gc_qs.latest('created_at')
                entry['goal_card'] = GoalCardSerializer(gc).data
            except GoalCard.DoesNotExist:
                pass
            result.append(entry)

        return Response(result)


class HODReviewGoalCardView(APIView):
    """PATCH /api/performance/goal-cards/<gc_id>/hod-review/ — HOD submits their review."""

    def patch(self, request, gc_id):
        try:
            gc = GoalCard.objects.get(id=gc_id)
        except GoalCard.DoesNotExist:
            return Response({'error': 'Goal card not found'}, status=404)

        if gc.status != 'manager_approved':
            return Response(
                {'error': f'Can only review manager-approved cards. Current status: {gc.status}'},
                status=400,
            )

        gc.hod_remarks = request.data.get('remarks', '')
        gc.hod_special_achievements = request.data.get('hod_special_achievements', gc.hod_special_achievements)
        gc.hod_promoted = request.data.get('hod_promoted', gc.hod_promoted)
        gc.hod_promoted_justification = request.data.get('hod_promoted_justification', gc.hod_promoted_justification)
        gc.hod_salary_correction = request.data.get('hod_salary_correction', gc.hod_salary_correction)
        gc.hod_salary_justification = request.data.get('hod_salary_justification', gc.hod_salary_justification)
        gc.status = 'hod_approved'
        gc.hod_reviewed_at = timezone.now()
        gc.save()

        ApprovalLog.objects.create(
            goal_card=gc,
            actor_role='hod',
            actor_name=request.data.get('hod_name', 'HOD'),
            action='hod_approved',
            comment=gc.hod_remarks,
        )

        return Response(GoalCardSerializer(gc).data)
