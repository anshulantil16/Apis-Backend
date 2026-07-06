# Generated migration for territory_mgmt

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='OrganizationData',
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
                'db_table': 'territory_organization_data',
                'ordering': ['sno'],
            },
        ),
        migrations.AddIndex(
            model_name='organizationdata',
            index=models.Index(fields=['designation'], name='territory_o_designa_idx'),
        ),
        migrations.AddIndex(
            model_name='organizationdata',
            index=models.Index(fields=['state'], name='territory_o_state_idx'),
        ),
        migrations.AddIndex(
            model_name='organizationdata',
            index=models.Index(fields=['zone'], name='territory_o_zone_idx'),
        ),
        migrations.AddIndex(
            model_name='organizationdata',
            index=models.Index(fields=['rm'], name='territory_o_rm_idx'),
        ),
        migrations.AddIndex(
            model_name='organizationdata',
            index=models.Index(fields=['batch_id'], name='territory_o_batch_i_idx'),
        ),
    ]
