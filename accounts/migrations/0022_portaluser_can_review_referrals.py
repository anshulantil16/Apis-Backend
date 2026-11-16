from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0021_portaluser_welcomed_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='portaluser',
            name='can_review_referrals',
            field=models.BooleanField(default=False),
        ),
    ]
