#!/usr/bin/env python
"""Test script to create sample data for offer letter testing."""
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from pms.models import PMSEmployee

# Clear existing test data
PMSEmployee.objects.filter(employee_id__startswith='EMP00').delete()

# Create test employees
employees = [
    {
        'employee_id': 'EMP001',
        'name': 'Rahul Sharma',
        'designation': 'Sales Manager',
        'department': 'Sales',
        'current_ctc': 600000.00,
    },
    {
        'employee_id': 'EMP002',
        'name': 'Priya Singh',
        'designation': 'Executive',
        'department': 'Marketing',
        'current_ctc': 450000.00,
    },
    {
        'employee_id': 'EMP003',
        'name': 'Amit Kumar',
        'designation': 'Associate',
        'department': 'Operations',
        'current_ctc': 280000.00,
    },
]

for emp_data in employees:
    emp = PMSEmployee.objects.create(**emp_data)
    print(f"[OK] Created: {emp.name} ({emp.employee_id}) - {emp.current_ctc:,.2f}")

print(f"\n[OK] Total employees created: {len(employees)}")
