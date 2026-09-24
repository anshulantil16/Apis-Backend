"""Clear out stories fetched before the link columns were widened.

Those rows were saved with `source_url` clipped to 500 characters. A clipped
Google News link is not a shorter link, it is a broken one -- Google answers it
with "400, the server cannot process the request because it is malformed" --
and roughly a third of each batch was over that length. Their `external_id`
was clipped the same way, so they would not even dedupe against a fresh fetch.

Nothing here is lost: every one of these came from a feed and the next run
brings back whatever is still current, now with a link that works.

Only machine-fetched rows go. Anything written by hand has `source_ref` null
and is left alone -- that is somebody's own work and is not re-fetchable.
"""
from django.db import migrations


def drop_fetched(apps, schema_editor):
    NewsItem = apps.get_model('noticeboard', 'NewsItem')
    NewsItem.objects.filter(source_ref__isnull=False).delete()


def noop(apps, schema_editor):
    """Nothing to restore: the rows this removed were broken, and the feeds
    are the source of truth for them."""


class Migration(migrations.Migration):

    dependencies = [
        ('noticeboard', '0007_alter_newsitem_external_id_alter_newsitem_image_url_and_more'),
    ]

    operations = [
        migrations.RunPython(drop_fetched, noop),
    ]
