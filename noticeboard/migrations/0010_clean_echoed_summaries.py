"""Blank the summaries on existing rows that only repeat their own headline.

The fetcher stopped storing these, but rows carried before that change still
have them, and on screen they read as the same sentence twice: once as the
headline and once in grey underneath it.

The rule is the fetcher's, inlined rather than imported -- a migration has to
keep working when the code it was written beside has moved on.
"""
import re

from django.db import migrations


def _norm(value):
    return re.sub(r'[^a-z0-9]+', ' ', (value or '').lower()).strip()


def clean(apps, schema_editor):
    NewsItem = apps.get_model('noticeboard', 'NewsItem')
    for n in NewsItem.objects.exclude(summary='').filter(source_ref__isnull=False):
        n_sum, n_title = _norm(n.summary), _norm(n.title)
        if not n_sum or not n_title:
            continue
        if n_sum == n_title:
            n.summary = ''
            n.save(update_fields=['summary'])
            continue
        if n_sum.startswith(n_title):
            rest = n_sum[len(n_title):].strip()
            if not rest or rest == _norm(n.source_name) or len(rest) < 10:
                n.summary = ''
                n.save(update_fields=['summary'])


def noop(apps, schema_editor):
    """The text removed was a duplicate of the headline beside it; there is
    nothing to put back."""


class Migration(migrations.Migration):

    dependencies = [
        ('noticeboard', '0009_news_sources_publish_by_default'),
    ]

    operations = [
        migrations.RunPython(clean, noop),
    ]
