from django.contrib import admin

from .models import WallPhoto


@admin.register(WallPhoto)
class WallPhotoAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'moderation_status', 'submitted_by_name', 'created_at')
    list_filter = ('moderation_status', 'category')
    search_fields = ('title', 'caption', 'submitted_by_name', 'submitted_by_email')
    readonly_fields = ('submitted_by_name', 'submitted_by_email', 'reviewed_by_name',
                       'reviewed_at', 'original_name', 'size_bytes', 'created_at')
