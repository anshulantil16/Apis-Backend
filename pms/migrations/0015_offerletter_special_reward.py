# Adds the one-time Special Reward fields to OfferLetter (additive only).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pms', '0014_offerletter_annexure_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='offerletter',
            name='special_reward',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
        migrations.AddField(
            model_name='offerletter',
            name='special_reward_note',
            field=models.CharField(blank=True, max_length=300),
        ),
    ]
