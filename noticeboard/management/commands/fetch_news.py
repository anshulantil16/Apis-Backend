"""Pull the Daily News feeds, meant to run on a schedule.

A plain management command driven by cron, for the same reason sync_hrms is
one: Celery was tried on this project and abandoned after repeated broker
problems with gunicorn's forked workers, and running one job a day is not
worth bringing that back.

Usage, once each morning before people arrive:
    crontab -e
    30 7 * * *  cd /var/www/html/apis-qa/backend && ./venv/bin/python manage.py fetch_news >> /var/log/apis-news.log 2>&1

Exits 0 even when a feed fails. A publisher being down is not something to
page anyone about at 7:30am, and the run's own output says what happened; the
same detail is on each source row in Admin Console, which is where somebody
would actually look.
"""
from django.core.management.base import BaseCommand

from noticeboard.models import NewsSource
from noticeboard.newsfeed import fetch_all


class Command(BaseCommand):
    help = 'Fetch the Daily News strip from its configured feeds.'

    def handle(self, *args, **opts):
        if not NewsSource.objects.filter(is_active=True).exists():
            self.stdout.write(self.style.WARNING(
                'No active news sources are set up - nothing to fetch. '
                'Add one in Admin Console > Dashboard Content > Daily News.'))
            return

        results = fetch_all()
        added = sum(r['added'] for r in results)
        failed = [r for r in results if r['error']]

        for r in results:
            if r['error']:
                self.stdout.write(self.style.WARNING(
                    f"  {r['source']}: {r['error']}"))
            else:
                self.stdout.write(
                    f"  {r['source']}: {r['found']} in feed, {r['added']} new")

        line = f'{added} new stor{"y" if added == 1 else "ies"} from {len(results)} source(s)'
        if failed:
            line += f', {len(failed)} source(s) failed'
        self.stdout.write(self.style.SUCCESS(line))

        if added:
            self.stdout.write(
                'Waiting for approval unless the source is set to publish '
                'automatically - review them in Admin Console.')
