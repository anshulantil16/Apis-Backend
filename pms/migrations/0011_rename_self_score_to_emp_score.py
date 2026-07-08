# Generated migration: Rename self_score to emp_score for new 4-way scoring formula

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pms', '0010_organization_structure'),
    ]

    operations = [
        migrations.RenameField(
            model_name='pmsemployee',
            old_name='self_score',
            new_name='emp_score',
        ),
    ]
