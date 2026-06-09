from django.db import migrations, models


class Migration(migrations.Migration):
    """Reverts migration 0014 — removes app_source from EmployeeProfile and PerformanceCycle."""

    dependencies = [
        ('performance', '0014_app_source_separation'),
    ]

    operations = [
        # Restore PerformanceCycle unique_together without app_source
        migrations.AlterUniqueTogether(
            name='performancecycle',
            unique_together={('quarter', 'fiscal_year')},
        ),
        migrations.RemoveField(model_name='performancecycle', name='app_source'),

        # Restore EmployeeProfile unique_together to just employee_id
        migrations.AlterUniqueTogether(
            name='employeeprofile',
            unique_together=set(),
        ),
        migrations.AlterField(
            model_name='employeeprofile',
            name='employee_id',
            field=models.CharField(max_length=50, unique=True),
        ),
        migrations.RemoveField(model_name='employeeprofile', name='app_source'),
    ]
