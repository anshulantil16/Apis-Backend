"""Month-wise arrears distribution: the master structure and the per-month rows.

Two AddFields only.

makemigrations also wanted to AlterField `id` to BigAutoField on offerletter
and pmssettings - the autodetector proposing that is cosmetic drift from
DEFAULT_AUTO_FIELD, not a change anyone asked for. That exact operation is
what killed migration 0020 on the MySQL server: pms_offerletterapproval holds
an int FK into pms_offerletter.id, and MySQL refuses to widen a column another
table's constraint points at. Deliberately left out; do not let a later
makemigrations quietly add it back.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pms', '0020_arrears_letters'),
    ]

    operations = [
        migrations.AddField(
            model_name='arrearsletter',
            name='master_breakup',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='arrearsletter',
            name='monthly_breakup',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
