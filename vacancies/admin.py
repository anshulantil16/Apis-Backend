from django.contrib import admin

from .models import Vacancy


@admin.register(Vacancy)
class VacancyAdmin(admin.ModelAdmin):
    list_display = ('title', 'department', 'function', 'location', 'state', 'type', 'status', 'created_at')
    list_filter = ('status', 'type', 'function', 'department')
    search_fields = ('title', 'department', 'location', 'state', 'reporting_manager')
