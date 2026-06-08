from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('eom', '0004_manager_scorecard_fields'),
    ]

    operations = [
        # Remove all manager fields (from 0001 and 0004)
        migrations.RemoveField(model_name='eomnomination', name='manager_remarks'),
        migrations.RemoveField(model_name='eomnomination', name='manager_reviewed_at'),
        migrations.RemoveField(model_name='eomnomination', name='manager_dim1_score'),
        migrations.RemoveField(model_name='eomnomination', name='manager_dim1_comments'),
        migrations.RemoveField(model_name='eomnomination', name='manager_dim2_score'),
        migrations.RemoveField(model_name='eomnomination', name='manager_dim2_comments'),
        migrations.RemoveField(model_name='eomnomination', name='manager_dim3_score'),
        migrations.RemoveField(model_name='eomnomination', name='manager_dim3_comments'),
        migrations.RemoveField(model_name='eomnomination', name='manager_dim4_score'),
        migrations.RemoveField(model_name='eomnomination', name='manager_dim4_comments'),
        migrations.RemoveField(model_name='eomnomination', name='manager_sustainability_desc'),
        migrations.RemoveField(model_name='eomnomination', name='manager_sustainability_bonus'),
        migrations.RemoveField(model_name='eomnomination', name='manager_sustainability_just'),
        migrations.RemoveField(model_name='eomnomination', name='manager_recommendation'),
        migrations.RemoveField(model_name='eomnomination', name='manager_panel_name'),

        # Update status choices (metadata only — no DB change needed)
        migrations.AlterField(
            model_name='eomnomination',
            name='status',
            field=models.CharField(
                choices=[
                    ('draft',        'Draft'),
                    ('submitted',    'Submitted'),
                    ('hod_approved', 'HOD Approved'),
                    ('hod_rejected', 'HOD Rejected'),
                    ('hr_finalized', 'HR Finalized'),
                ],
                default='draft', max_length=30,
            ),
        ),

        # Add HOD scorecard fields
        migrations.AddField(model_name='eomnomination', name='hod_dim1_score',           field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='eomnomination', name='hod_dim1_comments',         field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='hod_dim2_score',           field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='eomnomination', name='hod_dim2_comments',         field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='hod_dim3_score',           field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='eomnomination', name='hod_dim3_comments',         field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='hod_dim4_score',           field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='eomnomination', name='hod_dim4_comments',         field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='hod_sustainability_desc',   field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='hod_sustainability_bonus',  field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='eomnomination', name='hod_sustainability_just',   field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='hod_recommendation',        field=models.CharField(blank=True, max_length=20)),
        migrations.AddField(model_name='eomnomination', name='hod_panel_name',            field=models.CharField(blank=True, max_length=200)),
    ]
