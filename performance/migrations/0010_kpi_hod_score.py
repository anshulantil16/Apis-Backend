from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('performance', '0009_hod_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='kpi',
            name='hod_score',
            field=models.FloatField(blank=True, null=True),
        ),
    ]
