"""The reporting manager's name, beside the code we already kept.

Pocket HRMS sends both in one string -- "(HO0235)Pankaj Tripathi" -- and the
column was empty for all 2,365 employees until their support populated it on
15-10-2026. See accounts.services.hrms._manager.

Only the new field. `makemigrations` also wants to rewrite every `id` column
for DEFAULT_AUTO_FIELD; that drift is deliberate across this project and is
left alone here, as everywhere else.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0017_app_session'),
    ]

    operations = [
        migrations.AddField(
            model_name='portaluser',
            name='reporting_manager_name',
            field=models.CharField(blank=True, max_length=200),
        ),
    ]
