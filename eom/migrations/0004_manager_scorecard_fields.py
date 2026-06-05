from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('eom', '0003_eom_employee_and_auth'),
    ]

    operations = [
        migrations.AddField(model_name='eomnomination', name='manager_dim1_score',           field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='eomnomination', name='manager_dim1_comments',         field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='manager_dim2_score',           field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='eomnomination', name='manager_dim2_comments',         field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='manager_dim3_score',           field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='eomnomination', name='manager_dim3_comments',         field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='manager_dim4_score',           field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='eomnomination', name='manager_dim4_comments',         field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='manager_sustainability_desc',   field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='manager_sustainability_bonus',  field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='eomnomination', name='manager_sustainability_just',   field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='manager_recommendation',        field=models.CharField(blank=True, max_length=20)),
        migrations.AddField(model_name='eomnomination', name='manager_panel_name',            field=models.CharField(blank=True, max_length=200)),
    ]
