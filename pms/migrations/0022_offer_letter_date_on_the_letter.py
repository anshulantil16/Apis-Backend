# The id AlterFields Django also offers here are the known DEFAULT_AUTO_FIELD
# drift and are deliberately left out: generating them would rewrite the
# primary key of every PMS table for a field that nothing uses.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pms', '0021_arrears_month_wise'),
    ]

    operations = [
        migrations.AddField(
            model_name='offerletter',
            name='letter_date',
            field=models.DateField(blank=True, null=True),
        ),
    ]
