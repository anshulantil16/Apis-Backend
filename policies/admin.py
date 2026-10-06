from django.contrib import admin

from .models import BuiltInRemoval, PolicyDocument


@admin.register(PolicyDocument)
class PolicyDocumentAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'department', 'version', 'moderation_status',
                    'submitted_by_name', 'created_at')
    list_filter = ('moderation_status', 'category', 'department')
    search_fields = ('title', 'doc_approved_by', 'doc_reviewed_by', 'submitted_by_name')
    readonly_fields = ('submitted_by_name', 'submitted_by_email', 'reviewed_by_name',
                       'reviewed_at', 'original_name', 'size_bytes', 'pages', 'created_at')


@admin.register(BuiltInRemoval)
class BuiltInRemovalAdmin(admin.ModelAdmin):
    """Delete a row here to put that policy back on the page."""
    list_display = ('title', 'file', 'removed_by_name', 'removed_at')
    readonly_fields = ('removed_by_name', 'removed_at')
