from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('eom', '0005_remove_manager_add_hod_scorecard'),
    ]

    operations = [
        migrations.RemoveField(model_name='eomemployee', name='reporting_manager_id'),
    ]
