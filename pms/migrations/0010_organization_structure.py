# Generated migration for organization structure

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pms', '0009_offer_letter_send_tracking'),
    ]

    operations = [
        migrations.CreateModel(
            name='OrganizationStructure',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sno', models.IntegerField()),
                ('code', models.CharField(db_index=True, max_length=50, unique=True)),
                ('name', models.CharField(db_index=True, max_length=200)),
                ('designation', models.CharField(db_index=True, max_length=200)),
                ('hq', models.CharField(blank=True, max_length=200)),
                ('state', models.CharField(db_index=True, max_length=100)),
                ('zone', models.CharField(db_index=True, max_length=100)),
                ('rm', models.CharField(db_index=True, max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('batch_id', models.CharField(blank=True, db_index=True, max_length=50)),
            ],
            options={
                'ordering': ['sno'],
            },
        ),
        migrations.AddIndex(
            model_name='organizationstructure',
            index=models.Index(fields=['designation'], name='pms_organiz_designa_idx'),
        ),
        migrations.AddIndex(
            model_name='organizationstructure',
            index=models.Index(fields=['state'], name='pms_organiz_state_idx'),
        ),
        migrations.AddIndex(
            model_name='organizationstructure',
            index=models.Index(fields=['zone'], name='pms_organiz_zone_idx'),
        ),
        migrations.AddIndex(
            model_name='organizationstructure',
            index=models.Index(fields=['rm'], name='pms_organiz_rm_idx'),
        ),
        migrations.AddIndex(
            model_name='organizationstructure',
            index=models.Index(fields=['batch_id'], name='pms_organiz_batch_i_idx'),
        ),
    ]
