# Generated migration for offer letter send tracking

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('pms', '0008_merge_20260706_0722'),
    ]

    operations = [
        migrations.AddField(
            model_name='offerletter',
            name='batch_id',
            field=models.CharField(blank=True, db_index=True, max_length=50),
        ),
        migrations.AddField(
            model_name='offerletter',
            name='department',
            field=models.CharField(blank=True, db_index=True, max_length=200),
        ),
        migrations.CreateModel(
            name='OfferLetterSendLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('batch_id', models.CharField(db_index=True, max_length=50)),
                ('department', models.CharField(db_index=True, max_length=200)),
                ('employee_id', models.CharField(max_length=50)),
                ('employee_name', models.CharField(max_length=200)),
                ('email_address', models.EmailField(max_length=254)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('sent', 'Sent'), ('failed', 'Failed')], default='pending', max_length=20)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('error_message', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('offer_letter', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='send_log', to='pms.offerletter')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='offerlettersendlog',
            index=models.Index(fields=['batch_id'], name='pms_offerle_batch_i_idx'),
        ),
        migrations.AddIndex(
            model_name='offerlettersendlog',
            index=models.Index(fields=['department'], name='pms_offerle_departm_idx'),
        ),
        migrations.AddIndex(
            model_name='offerlettersendlog',
            index=models.Index(fields=['status'], name='pms_offerle_status_idx'),
        ),
    ]
