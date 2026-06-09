from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('eom', '0008_panel_role_and_scoring'),
    ]

    operations = [
        # Add dim6 for the split of old dim2 (APIS Values + Survey → now two separate dims)
        migrations.AddField(
            model_name='eomnomination',
            name='panel_dim6_score',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='eomnomination',
            name='panel_dim6_comments',
            field=models.TextField(blank=True),
        ),
    ]
