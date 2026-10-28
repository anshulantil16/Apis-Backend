"""Pull the employee master from Pocket HRMS, meant to run on a schedule.

Intentionally a plain Django management command driven by cron rather than a
new background-worker stack. Celery was tried for this project before (see
git history: async offer-letter generation) and abandoned after repeated
connection problems between gunicorn's forked workers and the broker - adding
that same machinery back just to run one job twice a day would be trading a
one-line crontab entry for a service that has already caused outages here.

Usage, twice a day:
    crontab -e
    0 6,18 * * *  cd /var/www/html/apis-qa/backend && ./venv/bin/python manage.py sync_hrms >> /var/log/apis-hrms-sync.log 2>&1

After the master is updated, the tools that keep their own copy of the
directory are refreshed from it in the same run -- TA/DA and the Help Desk.
They read PortalUser, so they have to follow this pull rather than race it
from a crontab entry of their own. SalesIQ needs nothing: it reads
PortalUser directly at sign-in and is current the moment this finishes.

Every run is logged to HrmsSyncLog regardless of source - the admin console's
"Sync history" table is the same table whether a person clicked the button or
cron ran this file at 6am, distinguished only by triggered_by.
"""
from django.core.management.base import BaseCommand, CommandError

from accounts.services import hrms


class Command(BaseCommand):
    help = 'Pull the employee master from Pocket HRMS and update PortalUser rows.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--since-days', type=int, default=None,
            help='Only ask Pocket HRMS for records changed in the last N days, '
                 'instead of the full directory. NOTE: this also skips the '
                 '"deactivate anyone HRMS no longer lists" step, which only '
                 'runs on a full, unfiltered sync - a partial pull is not '
                 'evidence that anyone has left. Use this for a quick manual '
                 'check, not for the scheduled job.')
        parser.add_argument(
            '--skip-tools', action='store_true',
            help='Pull the master but do not push it out to TA/DA and the '
                 'Help Desk afterwards.')

    def handle(self, *args, **opts):
        if not hrms.is_configured():
            # Exit 0, not an error: a cron job should not page anyone just
            # because the token has not been set up yet. Its own log line
            # says why nothing happened, which is what someone checking the
            # log file at 6am actually needs to see.
            self.stdout.write(self.style.WARNING(
                'Pocket HRMS is not configured (no POCKET_HRMS_TOKEN) - nothing to sync.'))
            return

        modified_since = None
        if opts['since_days'] is not None:
            from datetime import timedelta

            from django.utils import timezone
            modified_since = (timezone.now() - timedelta(days=opts['since_days'])).date()

        try:
            log = hrms.sync_employees(triggered_by='cron', modified_since=modified_since)
        except hrms.HrmsError as e:
            # CommandError exits 1 and prints cleanly, so cron's own failure
            # detection (a non-zero exit) sees this run as having failed,
            # without a Python traceback dumped into the log file.
            raise CommandError(f'Pocket HRMS sync failed: {e}') from e

        self.stdout.write(self.style.SUCCESS(log.message))

        if opts['skip_tools']:
            return
        for line in refresh_tool_directories():
            self.stdout.write(line)


def refresh_tool_directories():
    """Push the refreshed master out to the tools that keep their own copy.

    Run here rather than from their own cron entries, because the ordering
    is the whole point: these read PortalUser, so they have to follow the
    HRMS pull, not race it. One entry in the crontab, one guaranteed order.

    SalesIQ is deliberately absent. It has no copy of the directory -- it
    reads PortalUser when somebody signs in -- so it is already current the
    moment the sync above finishes, and a sync step for it would be
    pretending to do something.

    A failure in one tool is reported and does not stop the other, and
    neither stops the command: the master is already updated by this point,
    and exiting non-zero would tell cron the HRMS pull had failed when it
    had not.
    """
    lines = []
    try:
        from tada.sync import sync_from_master
        r = sync_from_master()
        lines.append(f"TA/DA: {r['created']} added, {r['updated']} updated, "
                     f"{len(r['needs_level'])} still need a level.")
    except Exception as e:                                   # noqa: BLE001
        lines.append(f'TA/DA directory refresh failed: {e}')
    try:
        from roompulse.directory import sync_from_directory
        r = sync_from_directory()
        lines.append('Help Desk: ' + ', '.join(f'{k} {v}' for k, v in sorted(r.items())
                                               if isinstance(v, int)))
    except Exception as e:                                   # noqa: BLE001
        lines.append(f'Help Desk directory refresh failed: {e}')
    return lines
