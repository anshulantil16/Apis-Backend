"""Widen `grade`, and give the sync somewhere to count rows the database refused.

Pocket HRMS treats grade as free text. 20 characters fitted the cadre codes
it was sized for ("O2", "M3") and not the label somebody entered on
12-10-2026, so MySQL refused the write and the whole sync rolled back --
twice a day, for 2,410 people, until it was noticed.

Only these two changes. `makemigrations` also wants to rewrite every `id`
column for DEFAULT_AUTO_FIELD; that drift is deliberate across this project
and is left alone here, as everywhere else.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0015_portaluser_can_view_confidential_vacancies'),
    ]

    operations = [
        migrations.AlterField(
            model_name='portaluser',
            name='grade',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name='hrmssynclog',
            name='skipped_bad_row',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
