from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Separates Performance Hub and Appraisal Hub data by adding an
    app_source field to EmployeeProfile and PerformanceCycle.
    Existing records get app_source='performance' by default.
    The unique constraint on employee_id becomes unique_together
    on (employee_id, app_source) so the same ID can exist in both hubs.
    """

    dependencies = [
        ('performance', '0013_add_uplift_and_competency_ratings'),
    ]

    operations = [
        # 1. Add app_source to EmployeeProfile (default='performance')
        migrations.AddField(
            model_name='employeeprofile',
            name='app_source',
            field=models.CharField(
                choices=[('performance', 'Performance Hub'), ('appraisal', 'Appraisal Hub')],
                db_index=True, default='performance', max_length=20,
            ),
        ),

        # 2. Drop the old unique constraint on employee_id alone
        migrations.AlterField(
            model_name='employeeprofile',
            name='employee_id',
            field=models.CharField(max_length=50),
        ),

        # 3. Add new unique_together on (employee_id, app_source)
        migrations.AlterUniqueTogether(
            name='employeeprofile',
            unique_together={('employee_id', 'app_source')},
        ),

        # 4. Add app_source to PerformanceCycle
        migrations.AddField(
            model_name='performancecycle',
            name='app_source',
            field=models.CharField(db_index=True, default='performance', max_length=20),
        ),

        # 5. Update PerformanceCycle unique_together to include app_source
        migrations.AlterUniqueTogether(
            name='performancecycle',
            unique_together={('quarter', 'fiscal_year', 'app_source')},
        ),
    ]
