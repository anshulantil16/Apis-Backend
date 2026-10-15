"""The Appraisal / EOM sign-in session.

Those tools kept their own OTP login from before the portal, and it issued
nothing: every request afterwards named an employee id in the URL and was
believed. This is the table that holds what the login now hands back.

Only the new model. `makemigrations` also wants to rewrite every `id` column
for DEFAULT_AUTO_FIELD; that drift is deliberate across this project and is
left alone here, as everywhere else.
"""
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0016_grade_fits_and_bad_rows_counted'),
    ]

    operations = [
        migrations.CreateModel(
            name='AppSession',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('app', models.CharField(db_index=True, max_length=20)),
                ('employee_id', models.CharField(db_index=True, max_length=50)),
                ('token_hash', models.CharField(db_index=True, max_length=64, unique=True)),
                ('user_agent', models.CharField(blank=True, max_length=300)),
                ('ip_address', models.CharField(blank=True, max_length=64)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('expires_at', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('last_seen_at', models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                'ordering': ['-last_seen_at'],
            },
        ),
    ]
