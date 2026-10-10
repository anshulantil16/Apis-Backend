from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0014_backfill_can_manage_tree'),
    ]

    operations = [
        migrations.AddField(
            model_name='portaluser',
            name='can_view_confidential_vacancies',
            field=models.BooleanField(default=False),
        ),
    ]
