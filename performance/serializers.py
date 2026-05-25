from rest_framework import serializers
from .models import (
    EmployeeProfile, PerformanceCycle, GoalCard,
    Goal, QuarterlyReview, ApprovalLog, GoalProgressUpdate, CompetencyRating
)


class EmployeeProfileSerializer(serializers.ModelSerializer):
    manager_name = serializers.SerializerMethodField()

    class Meta:
        model = EmployeeProfile
        fields = '__all__'

    def get_manager_name(self, obj):
        mgr = obj.reporting_manager
        return mgr.name if mgr else ''


class PerformanceCycleSerializer(serializers.ModelSerializer):
    class Meta:
        model = PerformanceCycle
        fields = '__all__'


class GoalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Goal
        fields = '__all__'


class ApprovalLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApprovalLog
        fields = '__all__'


class CompetencyRatingSerializer(serializers.ModelSerializer):
    competency_display = serializers.CharField(source='get_competency_display', read_only=True)

    class Meta:
        model = CompetencyRating
        fields = '__all__'


class GoalCardSerializer(serializers.ModelSerializer):
    goals = GoalSerializer(many=True, read_only=True)
    competency_ratings = CompetencyRatingSerializer(many=True, read_only=True)
    approval_logs = ApprovalLogSerializer(many=True, read_only=True)
    employee_name = serializers.CharField(source='employee.name', read_only=True)
    employee_id_str = serializers.CharField(source='employee.employee_id', read_only=True)
    employee_designation = serializers.CharField(source='employee.designation', read_only=True)
    employee_zone = serializers.CharField(source='employee.zone', read_only=True)
    cycle_name = serializers.CharField(source='cycle.name', read_only=True)
    total_weightage = serializers.IntegerField(read_only=True)
    final_weighted_score = serializers.FloatField(read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    review_data = serializers.SerializerMethodField()

    class Meta:
        model = GoalCard
        fields = '__all__'

    def get_review_data(self, obj):
        try:
            r = obj.review
            return {
                'id': r.id,
                'status': r.status,
                'status_display': r.get_status_display(),
                'final_weighted_score': float(r.final_weighted_score) if r.final_weighted_score else None,
                'performance_band': r.performance_band,
                'manager_overall_rating': r.manager_overall_rating,
                'hr_final_rating': r.hr_final_rating,
                'submitted_at': r.submitted_at,
            }
        except Exception:
            return None


class QuarterlyReviewSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='goal_card.employee.name', read_only=True)
    employee_id_str = serializers.CharField(source='goal_card.employee.employee_id', read_only=True)
    cycle_name = serializers.CharField(source='goal_card.cycle.name', read_only=True)
    performance_band_display = serializers.CharField(source='get_performance_band_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    # Goals nested so HR/manager can set per-goal ratings without a separate fetch
    goal_card_goals = serializers.SerializerMethodField()

    class Meta:
        model = QuarterlyReview
        fields = '__all__'

    def get_goal_card_goals(self, obj):
        return [
            {
                'id': g.id,
                'title': g.title,
                'category': g.category,
                'weightage': g.weightage,
                'kpi_metric': g.kpi_metric,
                'target_value': g.target_value,
                'self_completion_pct': g.self_completion_pct,
                'self_rating': g.self_rating,
                'self_comments': g.self_comments,
                'manager_rating': g.manager_rating,
                'manager_comments': g.manager_comments,
                'hr_rating': g.hr_rating,
                'hr_comments': g.hr_comments,
                'final_score': float(g.final_score) if g.final_score else None,
            }
            for g in obj.goal_card.goals.all()
        ]


class GoalProgressUpdateSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    goal_title = serializers.CharField(source='goal.title', read_only=True)

    class Meta:
        model = GoalProgressUpdate
        fields = '__all__'


class LeaderboardEntrySerializer(serializers.ModelSerializer):
    """Lightweight serializer for the leaderboard view."""
    review_score = serializers.SerializerMethodField()
    performance_band = serializers.SerializerMethodField()
    rank = serializers.SerializerMethodField()

    class Meta:
        model = EmployeeProfile
        fields = [
            'employee_id', 'name', 'designation', 'zone', 'subzone',
            'department', 'review_score', 'performance_band', 'rank'
        ]

    def get_review_score(self, obj):
        cycle_id = self.context.get('cycle_id')
        try:
            gc = GoalCard.objects.get(employee=obj, cycle_id=cycle_id)
            review = gc.review
            return float(review.final_weighted_score) if review.final_weighted_score else None
        except Exception:
            return None

    def get_performance_band(self, obj):
        cycle_id = self.context.get('cycle_id')
        try:
            gc = GoalCard.objects.get(employee=obj, cycle_id=cycle_id)
            return gc.review.get_performance_band_display()
        except Exception:
            return None

    def get_rank(self, obj):
        return self.context.get('ranks', {}).get(obj.employee_id)
