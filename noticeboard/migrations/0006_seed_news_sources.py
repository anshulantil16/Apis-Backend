"""Two feeds to start with, so the strip works the day this ships.

Both are created active but NOT auto-publishing: the scheduled run fills the
approval queue and somebody decides what goes up. That is deliberate for the
company search in particular -- run against real data while building this, a
search for the company name returned, alongside the AGM notice, several
stories about other food companies under an FSSAI scanner. None of that should
reach the company's own home page unread.

The windows differ because the subjects do. Trade news is plentiful, so a
fortnight keeps the strip current. News about one mid-cap company is not: the
same live check found two stories in a month, so a wider window is the
difference between a company feed and an empty one.

Both are ordinary rows. Delete them, edit the searches, or switch them off in
Admin Console > Dashboard Content > Daily News.
"""
from django.db import migrations

SOURCES = [
    {
        'name': 'Google News — Apis India',
        'kind': 'google_news',
        'query': '"Apis India" OR "Apis Himalaya" OR "Apis Natural"',
        'category': 'company',
        'max_per_run': 4,
        'max_age_days': 60,
    },
    {
        'name': 'Google News — honey & nutraceuticals',
        'kind': 'google_news',
        # Narrowed against live results. A bare "honey India" search returns
        # honey-trapping court reports and a celebrity's beehives; pairing the
        # subject words with trade words, and excluding the trap sense, leaves
        # APEDA export notices, processing-hub proposals and market pieces.
        'query': ('(honey OR apiculture OR beekeeping OR nutraceutical) '
                  '(industry OR export OR market OR FSSAI OR APEDA OR processing) '
                  'India -"honey trap" -honeytrap -trapping'),
        'category': 'industry',
        'max_per_run': 5,
        'max_age_days': 14,
    },
]


def seed(apps, schema_editor):
    NewsSource = apps.get_model('noticeboard', 'NewsSource')
    for spec in SOURCES:
        # get_or_create rather than create: re-running a migration on a database
        # that already has these should not give somebody two of each.
        NewsSource.objects.get_or_create(name=spec['name'], defaults={
            **spec, 'auto_publish': False, 'is_active': True,
        })


def unseed(apps, schema_editor):
    NewsSource = apps.get_model('noticeboard', 'NewsSource')
    # Only the rows this migration made, matched by name. Anything the company
    # added by hand is theirs and is left alone.
    NewsSource.objects.filter(name__in=[s['name'] for s in SOURCES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('noticeboard', '0005_newssource_max_age_days'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
