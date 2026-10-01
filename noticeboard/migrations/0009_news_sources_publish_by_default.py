"""Let the seeded feeds publish straight to the dashboard.

They were created needing approval, on the reasoning that a search for the
company's own name will eventually return a recall or a competitor's press
release. That reasoning still holds, and the run that seeded these proved it:
the first live fetch carried a honey-trapping court report and an FSSAI story
about other food companies.

It was traded away deliberately, for a reason that beats it in practice. An
approval queue that fills every morning is a queue that gets approved in bulk
without being read within about a fortnight, and then it is a gate in name
only while still being a daily chore. Publishing by default and removing the
occasional wrong one is the same amount of judgement applied at the only
moment somebody is actually looking.

What makes that safe enough to choose:

* The strip ages out on its own (NewsItem.STRIP_MAX_AGE_DAYS), so nothing sits
  there indefinitely even if nobody ever opens the console.
* Removal is one click in Admin Console > Dashboard Content > Daily News.
* Both feeds' searches were narrowed against live results first.
* This is a per-source switch, not a global one. Turning either back to
  review-first is a checkbox, and a feed added later starts off needing
  approval as before.
"""
from django.db import migrations

SEEDED = [
    'Google News — Apis India',
    'Google News — honey & nutraceuticals',
]


def publish_by_default(apps, schema_editor):
    NewsSource = apps.get_model('noticeboard', 'NewsSource')
    NewsSource.objects.filter(name__in=SEEDED).update(auto_publish=True)


def back_to_review(apps, schema_editor):
    NewsSource = apps.get_model('noticeboard', 'NewsSource')
    NewsSource.objects.filter(name__in=SEEDED).update(auto_publish=False)


class Migration(migrations.Migration):

    dependencies = [
        ('noticeboard', '0008_widen_news_links'),
    ]

    operations = [
        migrations.RunPython(publish_by_default, back_to_review),
    ]
