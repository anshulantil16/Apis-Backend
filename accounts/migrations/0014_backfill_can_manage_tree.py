"""Keep the people who already had add/remove able to add and remove.

Until now one grant, `can_edit_tree`, carried three different powers:
correcting a card, adding a person, and taking one off the chart. It is now
two grants, and the new one defaults to off -- which would quietly strip
adding and removing from everybody who has the old grant today.

Taking a capability away from somebody without being asked to is the wrong
default for a migration. So whoever can edit today can still manage, and a
Super Admin narrows it deliberately from the console. Going forward the new
grant starts off, which is the point of splitting them.
"""
from django.db import migrations


def carry_over(apps, schema_editor):
    PortalUser = apps.get_model('accounts', 'PortalUser')
    PortalUser.objects.filter(can_edit_tree=True).update(can_manage_tree=True)


def back(apps, schema_editor):
    # Nothing to undo: the column goes with 0013.
    pass


class Migration(migrations.Migration):
    dependencies = [('accounts', '0013_portaluser_can_manage_tree')]
    operations = [migrations.RunPython(carry_over, back)]
