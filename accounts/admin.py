from django.contrib import admin

from .models import TreeProfile


@admin.register(TreeProfile)
class TreeProfileAdmin(admin.ModelAdmin):
    """A dev/Super-Admin view of every APIS Tree card someone has edited —
    same purpose as wall.WallPhotoAdmin: a plain list of what exists,
    without needing to click through the whole org chart to find it."""
    list_display = ('person_id', 'name', 'role', 'department', 'is_new', 'is_hidden', 'parent_hod_id',
                    'updated_by_name', 'updated_at')
    list_filter = ('is_new', 'is_hidden')
    search_fields = ('person_id', 'name', 'role', 'department', 'updated_by_name', 'updated_by_email')
    readonly_fields = ('updated_by_name', 'updated_by_email', 'updated_at')
