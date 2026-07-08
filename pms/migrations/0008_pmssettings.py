from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pms', '0007_rename_self_score_emp_score'),
    ]

    operations = [
        migrations.CreateModel(
            name='PMSSettings',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('management_score', models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name_plural': 'PMS Settings',
            },
        ),
    ]
