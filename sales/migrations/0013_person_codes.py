"""The APIS / BIZOM ID beside each people column on the review sheet.

Nothing backfills these: they come off the sheet, so a re-upload of the
AOP file fills them and until then every count falls back to the name,
which is what it did before.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0012_exclude_scheme_cn'),
    ]

    operations = [
        migrations.AddField(
            model_name='salesrecord',
            name='asm_code',
            field=models.CharField(blank=True, db_index=True, max_length=60),
        ),
        migrations.AddField(
            model_name='salesrecord',
            name='rsm_code',
            field=models.CharField(blank=True, db_index=True, max_length=60),
        ),
        migrations.AddField(
            model_name='salesrecord',
            name='sales_head_code',
            field=models.CharField(blank=True, db_index=True, max_length=60),
        ),
    ]
