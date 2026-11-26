"""Birthday and work-anniversary wishes.

Hand-written, and additive on purpose: `makemigrations` also wanted to emit
`AlterField id` on portaluser, portalsession, portalotp, activitylog,
hrmssynclog and treeprofile, which is the project-wide DEFAULT_AUTO_FIELD
drift this repo deliberately leaves ungenerated. Those alterations rewrite the
primary key of every one of those tables; this feature is one new table and
has no business touching them.

So: CreateModel only. Nothing here reads or changes an existing row, which is
what makes it safe to run on the live portal.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('accounts', '0022_portaluser_can_review_referrals')]

    operations = [
        migrations.CreateModel(
            name='Wish',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True,
                                        serialize=False, verbose_name='ID')),
                ('sender_name', models.CharField(blank=True, max_length=200)),
                ('occasion', models.CharField(
                    choices=[('birthday', 'Birthday'),
                             ('anniversary', 'Work anniversary')], max_length=20)),
                ('occasion_date', models.DateField(db_index=True)),
                ('message', models.CharField(blank=True, max_length=280)),
                ('seen_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('recipient', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='wishes_received', to='accounts.portaluser')),
                ('sender', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='wishes_sent', to='accounts.portaluser')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(
            model_name='wish',
            index=models.Index(fields=['recipient', 'occasion_date'],
                               name='accounts_wi_recipie_10c9c4_idx'),
        ),
        migrations.AddConstraint(
            model_name='wish',
            constraint=models.UniqueConstraint(
                fields=('sender', 'recipient', 'occasion', 'occasion_date'),
                name='one_wish_per_sender_per_occasion'),
        ),
    ]
