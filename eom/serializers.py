from rest_framework import serializers
from .models import EOMEmployee, EOMCycle, EOMNomination


class EOMEmployeeSerializer(serializers.ModelSerializer):
    class Meta:
        model  = EOMEmployee
        fields = '__all__'


class EOMCycleSerializer(serializers.ModelSerializer):
    class Meta:
        model  = EOMCycle
        fields = '__all__'


class EOMNominationSerializer(serializers.ModelSerializer):
    employee_name        = serializers.CharField(source='employee.name',        read_only=True)
    employee_id_str      = serializers.CharField(source='employee.employee_id', read_only=True)
    employee_designation = serializers.CharField(source='employee.designation', read_only=True)
    employee_department  = serializers.CharField(source='employee.department',  read_only=True)
    employee_zone        = serializers.CharField(source='employee.zone',        read_only=True)
    cycle_name           = serializers.CharField(source='cycle.name',           read_only=True)

    class Meta:
        model  = EOMNomination
        fields = '__all__'
