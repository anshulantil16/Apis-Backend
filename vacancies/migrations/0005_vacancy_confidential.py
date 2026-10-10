from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vacancies', '0004_publish_existing'),
    ]

    operations = [
        migrations.AddField(
            model_name='vacancy',
            name='confidential',
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
