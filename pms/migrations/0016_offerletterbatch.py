# Adds the OfferLetterBatch progress-tracking table (new table, additive only).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pms', '0015_offerletter_special_reward'),
    ]

    operations = [
        migrations.CreateModel(
            name='OfferLetterBatch',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('batch_id', models.CharField(db_index=True, max_length=50, unique=True)),
                ('total', models.IntegerField(default=0)),
                ('processed', models.IntegerField(default=0)),
                ('generated', models.IntegerField(default=0)),
                ('emailed', models.IntegerField(default=0)),
                ('failed', models.IntegerField(default=0)),
                ('send_emails', models.BooleanField(default=False)),
                ('status', models.CharField(default='running', max_length=20)),
                ('errors', models.JSONField(blank=True, default=list)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
