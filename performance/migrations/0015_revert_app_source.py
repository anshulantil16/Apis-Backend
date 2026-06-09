from django.db import migrations, models


class Migration(migrations.Migration):
    """Reverts migration 0014 — removes app_source from EmployeeProfile and PerformanceCycle.
    Deletes appraisal-source duplicates before restoring unique constraint on employee_id.
    """

    dependencies = [
        ('performance', '0014_app_source_separation'),
    ]

    operations = [
        # Delete duplicate employees that were imported into the appraisal hub
        # (same employee_id but app_source='appraisal') so unique constraint can be restored
        migrations.RunSQL(
            sql="DELETE FROM performance_employeeprofile WHERE app_source = 'appraisal';",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql="DELETE FROM performance_performancecycle WHERE app_source = 'appraisal';",
            reverse_sql=migrations.RunSQL.noop,
        ),

        # Restore PerformanceCycle unique_together without app_source
        migrations.AlterUniqueTogether(
            name='performancecycle',
            unique_together={('quarter', 'fiscal_year')},
        ),
        migrations.RemoveField(model_name='performancecycle', name='app_source'),

        # Restore EmployeeProfile unique on employee_id
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
