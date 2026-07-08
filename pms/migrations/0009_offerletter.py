from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('pms', '0008_pmssettings'),
    ]

    operations = [
        migrations.CreateModel(
            name='OfferLetter',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('letter_type', models.CharField(choices=[('increment', 'Increment Letter'), ('promotion', 'Promotion Letter'), ('redesignation', 'Redesignation Letter'), ('combined', 'Combined Promotion & Increment Letter')], default='increment', max_length=20)),
                ('current_ctc', models.DecimalField(decimal_places=2, max_digits=14)),
                ('new_ctc', models.DecimalField(decimal_places=2, max_digits=14)),
                ('increment_pct', models.DecimalField(decimal_places=2, default=0, max_digits=5)),
                ('promotion_pct', models.DecimalField(decimal_places=2, default=0, max_digits=5)),
                ('effective_date', models.DateField()),
                ('old_designation', models.CharField(blank=True, max_length=200)),
                ('new_designation', models.CharField(blank=True, max_length=200)),
                ('performance_rating', models.CharField(blank=True, max_length=10)),
                ('grade_label', models.CharField(blank=True, max_length=100)),
                ('pdf_file', models.FileField(blank=True, null=True, upload_to='offer_letters/')),
                ('email_sent', models.BooleanField(default=False)),
                ('email_sent_at', models.DateTimeField(blank=True, null=True)),
                ('email_address', models.EmailField(blank=True, max_length=254)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('sent', 'Sent'), ('failed', 'Failed')], default='pending', max_length=20)),
                ('batch_id', models.CharField(blank=True, db_index=True, max_length=50)),
                ('department', models.CharField(blank=True, db_index=True, max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='offer_letters', to='pms.pmsemployee')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
