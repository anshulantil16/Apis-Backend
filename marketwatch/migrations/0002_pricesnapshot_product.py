from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('marketwatch', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='pricesnapshot',
            name='product',
            field=models.CharField(db_index=True, default='honey', max_length=20),
        ),
    ]
