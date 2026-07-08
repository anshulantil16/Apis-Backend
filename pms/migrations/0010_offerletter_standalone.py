from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('pms', '0009_offerletter'),
    ]

    operations = [
        migrations.AddField(
            model_name='offerletter',
            name='employee_code',
            field=models.CharField(blank=True, default='', max_length=50),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='offerletter',
            name='employee_name',
            field=models.CharField(blank=True, default='', max_length=200),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='offerletter',
            name='employee',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='offer_letters', to='pms.pmsemployee'),
        ),
    ]
