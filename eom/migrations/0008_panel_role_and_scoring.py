from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('eom', '0007_update_eomemployee_user_type_choices'),
    ]

    operations = [
        # ── Add 'panel' to EOMEmployee user_type choices ──────────────────────
        migrations.AlterField(
            model_name='eomemployee',
            name='user_type',
            field=models.CharField(
                choices=[
                    ('employee', 'Employee'),
                    ('hod',      'HOD'),
                    ('panel',    'Panel Member'),
                    ('hr',       'HR Admin'),
                ],
                default='employee', max_length=20,
            ),
        ),

        # ── Add panel_approved / panel_rejected to EOMNomination status ───────
        migrations.AlterField(
            model_name='eomnomination',
            name='status',
            field=models.CharField(
                choices=[
                    ('draft',          'Draft'),
                    ('submitted',      'Submitted'),
                    ('hod_approved',   'HOD Approved'),
                    ('hod_rejected',   'HOD Rejected'),
                    ('panel_approved', 'Panel Approved'),
                    ('panel_rejected', 'Panel Rejected'),
                    ('hr_finalized',   'HR Finalized'),
                ],
                default='draft', max_length=30,
            ),
        ),

        # ── Remove HOD dims 2-4 and sustainability (moved to Panel) ───────────
        migrations.RemoveField(model_name='eomnomination', name='hod_dim2_score'),
        migrations.RemoveField(model_name='eomnomination', name='hod_dim2_comments'),
        migrations.RemoveField(model_name='eomnomination', name='hod_dim3_score'),
        migrations.RemoveField(model_name='eomnomination', name='hod_dim3_comments'),
        migrations.RemoveField(model_name='eomnomination', name='hod_dim4_score'),
        migrations.RemoveField(model_name='eomnomination', name='hod_dim4_comments'),
        migrations.RemoveField(model_name='eomnomination', name='hod_sustainability_desc'),
        migrations.RemoveField(model_name='eomnomination', name='hod_sustainability_bonus'),
        migrations.RemoveField(model_name='eomnomination', name='hod_sustainability_just'),

        # ── Add Panel scorecard fields ────────────────────────────────────────
        migrations.AddField(model_name='eomnomination', name='panel_dim2_score',           field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='eomnomination', name='panel_dim2_comments',         field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='panel_dim3_score',           field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='eomnomination', name='panel_dim3_comments',         field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='panel_dim4_score',           field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='eomnomination', name='panel_dim4_comments',         field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='panel_dim5_score',           field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='eomnomination', name='panel_dim5_comments',         field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='panel_sustainability_desc',   field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='panel_sustainability_bonus',  field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='eomnomination', name='panel_sustainability_just',   field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='panel_recommendation',        field=models.CharField(blank=True, max_length=20)),
        migrations.AddField(model_name='eomnomination', name='panel_panel_name',            field=models.CharField(blank=True, max_length=200)),
        migrations.AddField(model_name='eomnomination', name='panel_remarks',               field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='panel_reviewed_at',           field=models.DateTimeField(blank=True, null=True)),
    ]
