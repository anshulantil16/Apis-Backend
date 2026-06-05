import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('eom', '0002_eom_nomination_form_fields'),
        ('performance', '0013_add_uplift_and_competency_ratings'),
    ]

    operations = [
        # 1. Drop unique constraint so we can swap the FK
        migrations.AlterUniqueTogether(
            name='eomnomination',
            unique_together=set(),
        ),

        # 2. Remove old employee FK (pointed to performance.EmployeeProfile)
        migrations.RemoveField(
            model_name='eomnomination',
            name='employee',
        ),

        # 3. Create EOMEmployee
        migrations.CreateModel(
            name='EOMEmployee',
            fields=[
                ('id',                   models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('employee_id',          models.CharField(max_length=50, unique=True)),
                ('name',                 models.CharField(max_length=200)),
                ('email',                models.EmailField(blank=True)),
                ('designation',          models.CharField(blank=True, max_length=100)),
                ('department',           models.CharField(blank=True, max_length=100)),
                ('zone',                 models.CharField(blank=True, max_length=100)),
                ('reporting_manager_id', models.CharField(blank=True, max_length=50)),
                ('hod_id',               models.CharField(blank=True, max_length=50)),
                ('user_type',            models.CharField(
                    choices=[('employee','Employee'),('manager','Manager'),('hod','HOD'),('hr','HR Admin')],
                    default='employee', max_length=20,
                )),
                ('is_active',  models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ['name']},
        ),

        # 4. Create EOMOTPToken
        migrations.CreateModel(
            name='EOMOTPToken',
            fields=[
                ('id',         models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('otp_code',   models.CharField(max_length=6)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField()),
                ('is_used',    models.BooleanField(default=False)),
                ('employee',   models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='otp_tokens',
                    to='eom.eomemployee',
                )),
            ],
            options={'ordering': ['-created_at']},
        ),

        # 5. Add new employee FK to EOMEmployee (null=True so ALTER TABLE works on existing table)
        migrations.AddField(
            model_name='eomnomination',
            name='employee',
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='nominations',
                to='eom.eomemployee',
            ),
        ),

        # 6. Make it non-nullable (safe: table is empty at this point)
        migrations.AlterField(
            model_name='eomnomination',
            name='employee',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='nominations',
                to='eom.eomemployee',
            ),
        ),

        # 7. Restore unique constraint
        migrations.AlterUniqueTogether(
            name='eomnomination',
            unique_together={('employee', 'cycle')},
        ),
    ]
