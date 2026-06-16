from rest_framework import serializers
from .models import (
    EmployeeProfile, PerformanceCycle, GoalCard,
    Goal, KPI, QuarterlyReview, ApprovalLog, GoalProgressUpdate, CompetencyRating,
    SupportDocument
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


class KPISerializer(serializers.ModelSerializer):
    class Meta:
        model = KPI
        fields = '__all__'


class GoalSerializer(serializers.ModelSerializer):
    kpis = KPISerializer(many=True, read_only=True)

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


class SupportDocumentSerializer(serializers.ModelSerializer):
    document_url = serializers.SerializerMethodField()

    class Meta:
        model = SupportDocument
        fields = ['id', 'file_name', 'document_url', 'uploaded_at']

    def get_document_url(self, obj):
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(obj.document.url)
        return obj.document.url


class GoalCardSerializer(serializers.ModelSerializer):
    goals = GoalSerializer(many=True, read_only=True)
    competency_ratings = CompetencyRatingSerializer(many=True, read_only=True)
    approval_logs = ApprovalLogSerializer(many=True, read_only=True)
    support_documents = serializers.SerializerMethodField()
    employee_name = serializers.CharField(source='employee.name', read_only=True)
    employee_id_str = serializers.CharField(source='employee.employee_id', read_only=True)
    employee_designation = serializers.CharField(source='employee.designation', read_only=True)
    employee_zone = serializers.CharField(source='employee.zone', read_only=True)
    cycle_name = serializers.CharField(source='cycle.name', read_only=True)
    total_weightage = serializers.FloatField(read_only=True)
    final_weighted_score = serializers.FloatField(read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    review_data = serializers.SerializerMethodField()

    def get_support_documents(self, obj):
        docs = obj.support_documents.all().order_by('-uploaded_at')
        return SupportDocumentSerializer(docs, many=True, context=self.context).data

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
    goal_card_goals = serializers.SerializerMethodField()

    class Meta:
        model = QuarterlyReview
        fields = '__all__'

    def get_goal_card_goals(self, obj):
        result = []
        for kra in obj.goal_card.goals.all():
            kra_data = {
                'id': kra.id,
                'category': kra.category,
                'title': kra.title,
                'description': kra.description,
                'kpis': [
                    {
                        'id': kpi.id,
                        'metric': kpi.metric,
                        'target_value': kpi.target_value,
                        'weightage': kpi.weightage,
                        'self_completion_pct': kpi.self_completion_pct,
                        'self_rating': kpi.self_rating,
                        'self_comments': kpi.self_comments,
                        'manager_rating': kpi.manager_rating,
                        'manager_comments': kpi.manager_comments,
                        'hr_rating': kpi.hr_rating,
                        'hr_comments': kpi.hr_comments,
                        'final_score': float(kpi.final_score) if kpi.final_score else None,
                    }
                    for kpi in kra.kpis.all()
                ]
            }
            result.append(kra_data)
        return result


class GoalProgressUpdateSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    kpi_metric = serializers.CharField(source='kpi.metric', read_only=True)

    class Meta:
        model = GoalProgressUpdate
        fields = '__all__'


class LeaderboardEntrySerializer(serializers.ModelSerializer):
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
