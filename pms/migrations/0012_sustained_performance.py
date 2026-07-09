from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pms', '0011_add_final_score_value'),
    ]

    operations = [
        migrations.AddField(
            model_name='pmsemployee',
            name='sustained_performance',
            field=models.BooleanField(default=False),
        ),
    ]
