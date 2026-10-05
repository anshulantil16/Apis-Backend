"""Take the accounting posting group back out of the sales channel.

'Gen. Bus. Posting Group' was in the channel column's alias list, so the
Pre-Sales Dump's DOMESTIC / EXPORT -- which is how a sale is POSTED, not how
it was sold -- landed in `channel` and showed up on the channel filter beside
the review sheet's GT and OT. One list, two vocabularies, describing two
different things.

The alias is gone, so a re-upload would fix it. This clears the rows already
loaded, so nobody has to re-upload to stop seeing "Domestic" in a filter.

Only invoice rows, and only those two exact values: they are the posting
group's whole vocabulary, and the review sheet's own channel values are left
alone. The dump's real channel split was never lost -- it is Customer Type
(General Trade, Modern Trade, Super Stockiest, Export, CPC, B2B), stored and
filterable under that name all along.
"""
from django.db import migrations

POSTING_GROUPS = ['DOMESTIC', 'EXPORT']


def clear(apps, schema_editor):
    SalesRecord = apps.get_model('sales', 'SalesRecord')
    SalesRecord.objects.filter(
        source='invoice', channel__in=POSTING_GROUPS).update(channel='')


def back(apps, schema_editor):
    # Not restorable, and not worth restoring: the value was never the
    # channel. A re-upload brings the column back wherever it is wanted.
    pass


class Migration(migrations.Migration):
    dependencies = [('sales', '0010_gl_account')]
    operations = [migrations.RunPython(clear, back)]
