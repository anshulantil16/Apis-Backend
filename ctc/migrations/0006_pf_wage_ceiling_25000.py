from django.db import migrations, models


def raise_ceiling(apps, schema_editor):
    # New rule: PF is 12% of Basic up to Rs 25,000, so never above Rs 3,000.
    apps.get_model('ctc', 'CtcSettings').objects.filter(pf_wage_ceiling=15000).update(pf_wage_ceiling=25000)


def lower_ceiling(apps, schema_editor):
    apps.get_model('ctc', 'CtcSettings').objects.filter(pf_wage_ceiling=25000).update(pf_wage_ceiling=15000)


class Migration(migrations.Migration):

    dependencies = [
        ('ctc', '0005_ctccandidate_number_of_children'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ctcsettings',
            name='pf_wage_ceiling',
            field=models.PositiveIntegerField(
                default=25000, help_text='PF wage ceiling per month (12% of Basic up to this; Rs 3,000 at 25,000)'),
        ),
        migrations.RunPython(raise_ceiling, lower_ceiling),
    ]
