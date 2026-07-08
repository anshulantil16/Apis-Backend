# Renames self_score -> emp_score (preserves existing data)

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('pms', '0006_rename_self_score_to_emp_score'),
    ]

    operations = [
        migrations.RenameField(
            model_name='pmsemployee',
            old_name='self_score',
            new_name='emp_score',
        ),
    ]
