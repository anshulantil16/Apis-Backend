from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('eom', '0001_initial_eom_models'),
    ]

    operations = [
        migrations.AddField(model_name='eomnomination', name='track',                  field=models.CharField(blank=True, max_length=20)),
        migrations.AddField(model_name='eomnomination', name='part_a_achievement',     field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='smart_specific',         field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='smart_measurable',       field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='smart_achievable',       field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='smart_relevant',         field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='smart_timebound',        field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='evidence_1_description', field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='evidence_1_source',      field=models.CharField(blank=True, max_length=300)),
        migrations.AddField(model_name='eomnomination', name='evidence_2_description', field=models.TextField(blank=True)),
        migrations.AddField(model_name='eomnomination', name='evidence_2_source',      field=models.CharField(blank=True, max_length=300)),
        migrations.AddField(model_name='eomnomination', name='declaration_agreed',     field=models.BooleanField(default=False)),
        migrations.AddField(model_name='eomnomination', name='signature_name',         field=models.CharField(blank=True, max_length=200)),
    ]
