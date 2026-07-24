# Adds the Annexure-A (Compensation Break-up) fields to OfferLetter.
# Additive only — new blank columns / JSON, no existing data touched.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pms', '0013_offerletter_assessment_offerletter_salutation_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='offerletter',
            name='function',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name='offerletter',
            name='cadre',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name='offerletter',
            name='grade',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name='offerletter',
            name='date_of_joining',
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name='offerletter',
            name='work_location',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name='offerletter',
            name='salary_breakup',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
