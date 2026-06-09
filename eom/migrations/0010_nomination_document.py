from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('eom', '0009_panel_dim6_split'),
    ]

    operations = [
        migrations.AddField(
            model_name='eomnomination',
            name='support_document',
            field=models.FileField(blank=True, null=True, upload_to='eom_docs/'),
        ),
        migrations.AddField(
            model_name='eomnomination',
            name='support_document_name',
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
