"""Clear fetched stories BEFORE the columns they sit in are resized.

Order matters here, and getting it wrong is what broke the QA deploy.

The next migration narrows `external_id` from 500 characters to 64, because it
is indexed and the raw feed ids are far too long to index on MySQL. Rows
already in the table hold 500-character ids. MySQL in strict mode refuses to
shorten a column when existing data would be truncated:

    (1265, "Data truncated for column 'external_id' at row 1")

SQLite, which is what runs locally, applies the same ALTER without complaint
and silently keeps the long values, so the test suite said the migration was
fine. It was fine on SQLite. QA is MySQL.

So the data goes first and the schema follows.

Nothing of value is lost. Every row removed here came from a feed, and its
`source_url` was clipped to 500 characters on the way in, which for a Google
News link means Google answers it with "400, malformed" -- these rows are the
broken ones the deploy was meant to fix. The next fetch brings back whatever is
still current, with links stored whole.

Anything written by hand has `source_ref` null and is left alone: that is
somebody's own work and no feed can bring it back.
"""
from django.db import migrations


def clear(apps, schema_editor):
    NewsItem = apps.get_model('noticeboard', 'NewsItem')
    NewsItem.objects.filter(source_ref__isnull=False).delete()
    # Belt and braces before the column shrinks: nothing may be left holding a
    # value longer than the new limit. Only the fetcher ever wrote this field,
    # so in practice the rows above were all of them.
    NewsItem.objects.exclude(external_id='').update(external_id='')


def noop(apps, schema_editor):
    """Nothing to restore: what this removed was broken, and the feeds are the
    source of truth for it."""


class Migration(migrations.Migration):

    dependencies = [
        ('noticeboard', '0006_seed_news_sources'),
    ]

    operations = [
        migrations.RunPython(clear, noop),
    ]
