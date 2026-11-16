# The "Welcome aboard" note for new joiners: when it was shown.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0020_hod_and_leave_chain'),
    ]

    operations = [
        migrations.AddField(
            model_name='portaluser',
            name='welcomed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
