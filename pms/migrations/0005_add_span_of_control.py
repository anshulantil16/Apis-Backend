# Generated migration to add span_of_control field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pms', '0004_remove_pmsemployee_fy_prev1_grade_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='pmsemployee',
            name='span_of_control',
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
