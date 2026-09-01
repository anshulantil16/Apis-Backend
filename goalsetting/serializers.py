from rest_framework import serializers

from .models import (EmployeeProfile, GoalCycle, GoalPlan, GoalKPI, KRA,
                     PlanEvent, PlanVersion)


class EmployeeSerializer(serializers.ModelSerializer):
    manager_name = serializers.SerializerMethodField()
    hod_name = serializers.SerializerMethodField()

    class Meta:
        model = EmployeeProfile
        fields = ['id', 'employee_id', 'name', 'email', 'phone', 'designation',
                  'department', 'zone', 'subzone', 'reporting_manager_id', 'hod_id',
                  'user_type', 'is_active', 'joined_date', 'manager_name', 'hod_name']

    def get_manager_name(self, obj):
        m = obj.manager
        return m.name if m else ''

    def get_hod_name(self, obj):
        h = obj.hod
        return h.name if h else ''


class CycleSerializer(serializers.ModelSerializer):
    plan_count = serializers.SerializerMethodField()

    class Meta:
        model = GoalCycle
        fields = ['id', 'name', 'fiscal_year', 'starts_on', 'ends_on',
                  'submission_deadline', 'status', 'created_by', 'created_at',
                  'plan_count']

    def get_plan_count(self, obj):
        return obj.goal_plans.count()


class KPISerializer(serializers.ModelSerializer):
    class Meta:
        model = GoalKPI
        fields = ['id', 'metric', 'weightage', 'frequency', 'unit_of_measurement',
                  'parameter_type', 'data_source', 'target_value', 'order']


class KRASerializer(serializers.ModelSerializer):
    kpis = KPISerializer(many=True, read_only=True)

    class Meta:
        model = KRA
        fields = ['id', 'category', 'title', 'description', 'order', 'kpis']


class VersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanVersion
        fields = ['id', 'version_no', 'actor_role', 'actor_name', 'actor_employee_id',
                  'action', 'note', 'kras', 'changes', 'total_weightage', 'created_at']


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanEvent
        fields = ['id', 'actor_role', 'actor_name', 'action', 'note', 'created_at']


class PlanSerializer(serializers.ModelSerializer):
    """A plan with everything a screen needs, including its whole history.

    The versions ride along rather than sitting behind a second request: every
    screen in this product shows what changed, so fetching a plan without its
    history would just mean two calls every time.
    """

    kras = KRASerializer(many=True, read_only=True)
    versions = VersionSerializer(many=True, read_only=True)
    events = EventSerializer(many=True, read_only=True)
    employee_name = serializers.CharField(source='employee.name', read_only=True)
    employee_code = serializers.CharField(source='employee.employee_id', read_only=True)
    designation = serializers.CharField(source='employee.designation', read_only=True)
    department = serializers.CharField(source='employee.department', read_only=True)
    cycle_name = serializers.CharField(source='cycle.name', read_only=True)
    cycle_status = serializers.CharField(source='cycle.status', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    total_weightage = serializers.FloatField(read_only=True)
    kpi_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = GoalPlan
        fields = ['id', 'employee', 'employee_name', 'employee_code', 'designation',
                  'department', 'cycle', 'cycle_name', 'cycle_status', 'status',
                  'status_label', 'employee_note', 'manager_note', 'hod_note',
                  'employee_acceptance_note', 'created_at', 'submitted_at',
                  'manager_acted_at', 'hod_acted_at', 'accepted_at',
                  'total_weightage', 'kpi_count', 'kras', 'versions', 'events']


class PlanSummarySerializer(serializers.ModelSerializer):
    """The list view. Deliberately without versions - a team of forty managers
    would otherwise pull every snapshot of every plan to render a table."""

    employee_name = serializers.CharField(source='employee.name', read_only=True)
    employee_code = serializers.CharField(source='employee.employee_id', read_only=True)
    designation = serializers.CharField(source='employee.designation', read_only=True)
    department = serializers.CharField(source='employee.department', read_only=True)
    cycle_name = serializers.CharField(source='cycle.name', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    total_weightage = serializers.FloatField(read_only=True)
    kpi_count = serializers.IntegerField(read_only=True)
    kra_count = serializers.SerializerMethodField()
    version_count = serializers.SerializerMethodField()

    class Meta:
        model = GoalPlan
        fields = ['id', 'employee_name', 'employee_code', 'designation', 'department',
                  'cycle', 'cycle_name', 'status', 'status_label', 'total_weightage',
                  'kpi_count', 'kra_count', 'version_count', 'submitted_at',
                  'manager_acted_at', 'hod_acted_at', 'accepted_at']

    def get_kra_count(self, obj):
        return obj.kras.count()

    def get_version_count(self, obj):
        return obj.versions.count()
