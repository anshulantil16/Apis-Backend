from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('eom', '0006_remove_reporting_manager_from_eomemployee'),
    ]

    operations = [
        migrations.AlterField(
            model_name='eomemployee',
            name='user_type',
            field=models.CharField(
                choices=[
                    ('employee', 'Employee'),
                    ('hod',      'HOD'),
                    ('hr',       'HR Admin'),
                ],
                default='employee',
                max_length=20,
            ),
        ),
    ]
