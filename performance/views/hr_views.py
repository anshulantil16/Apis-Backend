"""
HR Admin-facing API views.
HR has full access: employee import, cycle management, final calibration, leaderboard.
"""
import pandas as pd
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser
from django.utils import timezone

from ..models import (
    EmployeeProfile, PerformanceCycle, GoalCard,
    Goal, QuarterlyReview, ApprovalLog, CompetencyRating
)
from ..serializers import (
    EmployeeProfileSerializer, PerformanceCycleSerializer,
    GoalCardSerializer, QuarterlyReviewSerializer, LeaderboardEntrySerializer
)


class EmployeeImportView(APIView):
    """POST /api/performance/employees/import/ — HR uploads employee master Excel/CSV."""
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        file = request.FILES.get('file')
        if not file:
            return Response({'error': 'No file uploaded'}, status=400)

        try:
            file_name = file.name.lower()
            if file_name.endswith('.csv'):
                try:
                    df = pd.read_csv(file)
                except UnicodeDecodeError:
                    file.seek(0)
                    df = pd.read_csv(file, encoding='latin1')
            else:
                df = pd.read_excel(file)

            df.columns = df.columns.astype(str).str.strip().str.lower().str.replace(' ', '_')
            df = df.fillna('')

        except Exception as e:
            return Response({'error': f'Could not read file: {str(e)}'}, status=400)

        # Column aliases for flexibility
        COLUMN_MAP = {
            'employee_id': ['employee_id', 'emp_id', 'emp_code', 'user_id'],
            'name': ['name', 'employee_name', 'user_name'],
            'email': ['email', 'email_id'],
            'phone': ['phone', 'phone_number', 'mobile'],
            'designation': ['designation', 'role'],
            'department': ['department', 'dept'],
            'zone': ['zone'],
            'subzone': ['subzone', 'sub_zone'],
            'reporting_manager_id': ['reporting_manager_id', 'reporting_to', 'manager_id'],
            'hod_id': ['hod_id', 'hod', 'head_of_department_id'],
            'user_type': ['user_type', 'type'],
            'joined_date': ['joined_date', 'joining_date', 'doj'],
        }

        def get_col(df, aliases):
            for alias in aliases:
                if alias in df.columns:
                    return alias
            return None

        created, updated, errors = 0, 0, []

        for i, row in df.iterrows():
            def val(field):
                col = get_col(df, COLUMN_MAP[field])
                return str(row[col]).strip() if col and str(row[col]).strip() else ''

            emp_id = val('employee_id')
            if not emp_id:
                errors.append(f'Row {i+2}: Missing employee_id')
                continue

            name = val('name')
            if not name:
                errors.append(f'Row {i+2}: Missing name for {emp_id}')
                continue

            user_type_raw = val('user_type').lower()
            if 'hr' in user_type_raw:
                user_type = 'hr'
            elif 'hod' in user_type_raw or 'head' in user_type_raw:
                user_type = 'hod'
            elif 'manager' in user_type_raw or 'mgr' in user_type_raw:
                user_type = 'manager'
            else:
                user_type = 'field_force'

            joined_str = val('joined_date')
            joined_date = None
            if joined_str:
                try:
                    import dateutil.parser
                    joined_date = dateutil.parser.parse(joined_str).date()
                except Exception:
                    pass

            emp, was_created = EmployeeProfile.objects.update_or_create(
                employee_id=emp_id,
                defaults={
                    'name': name,
                    'email': val('email'),
                    'phone': val('phone'),
                    'designation': val('designation'),
                    'department': val('department'),
                    'zone': val('zone'),
                    'subzone': val('subzone'),
                    'reporting_manager_id': val('reporting_manager_id'),
                    'hod_id': val('hod_id'),
                    'user_type': user_type,
                    'joined_date': joined_date,
                    'is_active': True,
                }
            )
            if was_created:
                created += 1
            else:
                updated += 1

        return Response({
            'message': f'Import complete. {created} created, {updated} updated.',
            'errors': errors,
            'created': created,
            'updated': updated,
        })


class EmployeeListView(APIView):
    """GET /api/performance/employees/ — list all employees with optional filters."""

    def get(self, request):
        qs = EmployeeProfile.objects.filter(is_active=True)
        zone = request.query_params.get('zone')
        user_type = request.query_params.get('user_type')
        if zone:
            qs = qs.filter(zone__icontains=zone)
        if user_type:
            qs = qs.filter(user_type=user_type)
        return Response(EmployeeProfileSerializer(qs, many=True).data)


class CycleListCreateView(APIView):
    """
    GET  /api/performance/cycles/ — list all cycles
    POST /api/performance/cycles/ — HR creates a new cycle
    """

    def get(self, request):
        cycles = PerformanceCycle.objects.all()
        return Response(PerformanceCycleSerializer(cycles, many=True).data)

    def post(self, request):
        serializer = PerformanceCycleSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(created_by=request.data.get('created_by', 'HR'))
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)


class CycleUpdateView(APIView):
    """PATCH /api/performance/cycles/<cycle_id>/ — update cycle status."""

    def patch(self, request, cycle_id):
        try:
            cycle = PerformanceCycle.objects.get(id=cycle_id)
        except PerformanceCycle.DoesNotExist:
            return Response({'error': 'Cycle not found'}, status=404)

        allowed = ['status', 'goal_setting_deadline', 'review_start_date', 'review_deadline']
        for field in allowed:
            if field in request.data:
                setattr(cycle, field, request.data[field])
        cycle.save()
        return Response(PerformanceCycleSerializer(cycle).data)


class HRFinalizeReviewView(APIView):
    """PATCH /api/performance/reviews/<review_id>/hr-finalize/ — HR calibrates and publishes final scores."""

    def patch(self, request, review_id):
        try:
            review = QuarterlyReview.objects.get(id=review_id)
        except QuarterlyReview.DoesNotExist:
            return Response({'error': 'Review not found'}, status=404)

        if review.goal_card.status != 'hod_approved':
            return Response(
                {'error': f'Goal card must be HOD-approved before HR can finalize. Current status: {review.goal_card.status}'},
                status=400,
            )

        review.hr_final_rating = request.data.get('hr_final_rating')
        review.hr_comments = request.data.get('hr_comments', '')
        review.functional_head_remarks = request.data.get('functional_head_remarks', '')
        review.functional_head_rating = request.data.get('functional_head_rating') or None
        review.management_approval_remarks = request.data.get('management_approval_remarks', '')
        review.management_approval_rating = request.data.get('management_approval_rating') or None

        # Per-goal HR ratings
        goal_ratings = request.data.get('goal_ratings', [])
        for gr in goal_ratings:
            try:
                goal = Goal.objects.get(id=gr['goal_id'], goal_card=review.goal_card)
                goal.hr_rating = gr.get('hr_rating')
                goal.hr_comments = gr.get('hr_comments', '')
                goal.final_score = goal.compute_final_score()
                goal.save()
            except Goal.DoesNotExist:
                pass

        # Calculate final weighted score
        goals = review.goal_card.goals.all()
        if goals:
            total_weight = sum(g.weightage for g in goals)
            if total_weight > 0:
                weighted = sum(
                    (float(g.final_score or 0)) * g.weightage
                    for g in goals
                )
                review.final_weighted_score = round(weighted / total_weight, 2)
                review.performance_band = review.compute_band(float(review.final_weighted_score))

        publish = request.data.get('publish', False)
        if publish:
            review.status = 'published'
            review.published_at = timezone.now()
        else:
            review.status = 'hr_finalized'
        review.hr_finalized_at = timezone.now()
        review.save()

        ApprovalLog.objects.create(
            goal_card=review.goal_card,
            actor_role='hr',
            actor_name=request.data.get('hr_name', 'HR'),
            action='published' if publish else 'hr_finalized',
            comment=review.hr_comments
        )

        return Response(QuarterlyReviewSerializer(review).data)


class OrgOverviewView(APIView):
    """GET /api/performance/org/overview/ — HR org-wide statistics for a cycle."""

    def get(self, request):
        cycle_id = request.query_params.get('cycle_id')
        if not cycle_id:
            return Response({'error': 'cycle_id required'}, status=400)

        gcs = GoalCard.objects.filter(cycle_id=cycle_id).select_related('employee')
        total = gcs.count()

        status_counts = {}
        for gc in gcs:
            s = gc.status
            status_counts[s] = status_counts.get(s, 0) + 1

        reviews = QuarterlyReview.objects.filter(goal_card__cycle_id=cycle_id)
        review_status_counts = {}
        for r in reviews:
            s = r.status
            review_status_counts[s] = review_status_counts.get(s, 0) + 1

        published_reviews = reviews.filter(status='published', final_weighted_score__isnull=False)
        scores = [float(r.final_weighted_score) for r in published_reviews]
        avg_score = round(sum(scores) / len(scores), 2) if scores else None

        band_counts = {}
        for r in published_reviews:
            b = r.performance_band
            band_counts[b] = band_counts.get(b, 0) + 1

        return Response({
            'total_employees': total,
            'goal_card_status': status_counts,
            'review_status': review_status_counts,
            'avg_score': avg_score,
            'band_distribution': band_counts,
        })


class LeaderboardView(APIView):
    """GET /api/performance/leaderboard/?cycle_id=<id> — ranked leaderboard."""

    def get(self, request):
        cycle_id = request.query_params.get('cycle_id')
        if not cycle_id:
            return Response({'error': 'cycle_id required'}, status=400)

        published_gcs = GoalCard.objects.filter(
            cycle_id=cycle_id,
            review__status='published'
        ).select_related('employee', 'review').order_by(
            '-review__final_weighted_score'
        )

        leaderboard = []
        for rank, gc in enumerate(published_gcs, 1):
            review = gc.review
            emp = gc.employee
            leaderboard.append({
                'rank': rank,
                'employee_id': emp.employee_id,
                'name': emp.name,
                'designation': emp.designation,
                'zone': emp.zone,
                'subzone': emp.subzone,
                'department': emp.department,
                'final_score': float(review.final_weighted_score),
                'performance_band': review.performance_band,
                'performance_band_display': review.get_performance_band_display(),
            })

        return Response(leaderboard)


class AllGoalCardsView(APIView):
    """GET /api/performance/goal-cards/?cycle_id=<id>&status=<status> — HR sees all."""

    def get(self, request):
        qs = GoalCard.objects.all().select_related('employee', 'cycle')
        cycle_id = request.query_params.get('cycle_id')
        gc_status = request.query_params.get('status')
        zone = request.query_params.get('zone')

        if cycle_id:
            qs = qs.filter(cycle_id=cycle_id)
        if gc_status:
            qs = qs.filter(status=gc_status)
        if zone:
            qs = qs.filter(employee__zone__icontains=zone)

        return Response(GoalCardSerializer(qs, many=True).data)


class AllReviewsView(APIView):
    """GET /api/performance/reviews/?cycle_id=<id> — HR sees all quarterly reviews."""

    def get(self, request):
        qs = QuarterlyReview.objects.all().select_related(
            'goal_card__employee', 'goal_card__cycle'
        )
        cycle_id = request.query_params.get('cycle_id')
        rev_status = request.query_params.get('status')

        if cycle_id:
            qs = qs.filter(goal_card__cycle_id=cycle_id)
        if rev_status:
            qs = qs.filter(status=rev_status)

        return Response(QuarterlyReviewSerializer(qs, many=True).data)


class ResetDatabaseView(APIView):
    """POST /api/performance/org/reset/ — HR resets the entire database (employees, goal cards, cycles, reviews, logs)."""

    def post(self, request):
        try:
            ApprovalLog.objects.all().delete()
            QuarterlyReview.objects.all().delete()
            CompetencyRating.objects.all().delete()
            Goal.objects.all().delete()
            GoalCard.objects.all().delete()
            PerformanceCycle.objects.all().delete()
            EmployeeProfile.objects.all().delete()
            return Response({'message': 'All performance database tables successfully cleared!'})
        except Exception as e:
            return Response({'error': f'Failed to reset database: {str(e)}'}, status=500)

