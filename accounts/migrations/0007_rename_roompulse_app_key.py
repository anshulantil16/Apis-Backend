# Renames the 'roompulse' app-access grant to 'helpdesk', matching the
# frontend's QuickAccessId rename (RoomPulse -> HelpDesk). Without this,
# every user previously granted AdminPulse/Help Desk access still carries
# the old string in PortalUser.app_access and the tool silently disappears
# from their sidebar, since the frontend now checks for 'helpdesk'.
from django.db import migrations


def rename_forward(apps, schema_editor):
    # Iterates every row rather than filtering by app_access__contains=...:
    # JSONField list-containment lookups differ enough between SQLite (local)
    # and MySQL (QA) that a plain Python pass is the safer, backend-agnostic
    # way to touch every row exactly once.
    PortalUser = apps.get_model('accounts', 'PortalUser')
    for user in PortalUser.objects.all():
        if 'roompulse' in (user.app_access or []):
            user.app_access = ['helpdesk' if a == 'roompulse' else a for a in user.app_access]
            user.save(update_fields=['app_access'])


def rename_backward(apps, schema_editor):
    PortalUser = apps.get_model('accounts', 'PortalUser')
    for user in PortalUser.objects.all():
        if 'helpdesk' in (user.app_access or []):
            user.app_access = ['roompulse' if a == 'helpdesk' else a for a in user.app_access]
            user.save(update_fields=['app_access'])


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_activitylog'),
    ]

    operations = [
        migrations.RunPython(rename_forward, rename_backward),
    ]
