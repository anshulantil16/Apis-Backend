from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pms', '0010_offerletter_standalone'),
    ]

    operations = [
        migrations.AddField(
            model_name='pmsemployee',
            name='final_score_value',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True),
        ),
    ]
