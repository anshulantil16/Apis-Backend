from django.contrib import admin

from .models import CtcCandidate, CtcSettings, CtcUpload


class CtcCandidateInline(admin.TabularInline):
    model = CtcCandidate
    extra = 0
    fields = ('row_number', 'candidate_name', 'category', 'department', 'designation', 'grade',
              'cadre', 'work_location', 'ctc')


@admin.register(CtcUpload)
class CtcUploadAdmin(admin.ModelAdmin):
    list_display = ('original_name', 'row_count', 'uploaded_by_name', 'created_at')
    search_fields = ('original_name', 'uploaded_by_name')
    readonly_fields = ('uploaded_by_name', 'row_count', 'created_at')
    inlines = [CtcCandidateInline]


@admin.register(CtcCandidate)
class CtcCandidateAdmin(admin.ModelAdmin):
    list_display = ('candidate_name', 'category', 'department', 'designation', 'grade', 'cadre',
                    'work_location', 'ctc', 'upload')
    list_filter = ('department', 'grade', 'cadre', 'work_location')
    search_fields = ('candidate_name', 'department', 'designation')


@admin.register(CtcSettings)
class CtcSettingsAdmin(admin.ModelAdmin):
    readonly_fields = ('updated_at', 'updated_by_name')
