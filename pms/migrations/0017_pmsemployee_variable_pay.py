# Adds Current Variable Pay to PMSEmployee (additive, nullable — no data touched).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pms', '0016_offerletterbatch'),
    ]

    operations = [
        migrations.AddField(
            model_name='pmsemployee',
            name='variable_pay',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
    ]
