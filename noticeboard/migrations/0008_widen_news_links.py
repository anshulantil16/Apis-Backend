"""Widen the link columns, and narrow the dedupe key to a hash.

Runs after 0007 has emptied the fetched rows, because MySQL will not shorten
`external_id` while a row still holds a value longer than the new limit.

- source_url / image_url: 500 -> 2000. Google News article links are opaque
  encoded ids; in one real feed 13 of 39 were over 500 characters and the
  longest was 824. A clipped link is a broken link.
- external_id: 500 -> 64, now a SHA-256 of the feed's id rather than the id
  itself. The column is indexed and MySQL's utf8mb4 index limit is 3072 bytes,
  so a column wide enough to hold the raw id could carry no index at all.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('noticeboard', '0007_clear_fetched_news'),
    ]

    operations = [
        migrations.AlterField(
            model_name='newsitem',
            name='external_id',
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AlterField(
            model_name='newsitem',
            name='image_url',
            field=models.URLField(blank=True, max_length=2000),
        ),
        migrations.AlterField(
            model_name='newsitem',
            name='source_url',
            field=models.URLField(blank=True, max_length=2000),
        ),
    ]
