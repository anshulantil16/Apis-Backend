from django.contrib import admin

from .models import EmployeeReferral


@admin.register(EmployeeReferral)
class EmployeeReferralAdmin(admin.ModelAdmin):
    list_display = ('candidate_name', 'position_applied_for', 'referrer_name', 'recommendation', 'status', 'created_at')
    list_filter = ('status', 'recommendation', 'candidate_department')
    search_fields = ('candidate_name', 'referrer_name', 'candidate_email', 'referrer_email', 'position_applied_for')
    readonly_fields = [f.name for f in EmployeeReferral._meta.fields if f.name not in ('verified_by', 'verification_date', 'hr_remarks', 'status')]
