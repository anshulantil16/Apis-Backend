from django.contrib import admin
from .models import OrganizationData


@admin.register(OrganizationData)
class OrganizationDataAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'designation', 'zone', 'state', 'rm', 'created_at')
    list_filter = ('designation', 'zone', 'state', 'rm', 'created_at')
    search_fields = ('code', 'name', 'rm')
    readonly_fields = ('created_at', 'updated_at')
