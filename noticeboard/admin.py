from django.contrib import admin

from .models import Announcement, Holiday, HolidayZone


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ('title', 'tone', 'announced_on', 'expires_on', 'pinned', 'moderation_status')
    list_filter = ('moderation_status', 'tone', 'pinned')
    search_fields = ('title', 'body', 'submitted_by_name')


@admin.register(HolidayZone)
class HolidayZoneAdmin(admin.ModelAdmin):
    list_display = ('label', 'key', 'sort_order')


@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ('name', 'date', 'zone', 'type')
    list_filter = ('zone', 'type')
    search_fields = ('name',)
