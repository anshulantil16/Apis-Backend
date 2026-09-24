"""Pulling the Daily News strip from real feeds, rather than typing it in.

Why RSS and not a news API: every news API worth using wants a key, a signup
and, above the free tier, a bill — for a strip on an intranet. Google News
publishes any search as RSS with none of that, and a trade publication's own
feed is better still where one exists. Both are XML, so parsing is stdlib
ElementTree and nothing new has to be installed on the server.

Two things this module is careful about.

**Nothing it brings in publishes itself.** Fetched stories are created PENDING
like everything else that reaches the dashboard. A search for the company's own
name will eventually return a recall, a lawsuit or a competitor's press
release, and none of those should put themselves on the company home page at
6am because cron ran. A source can be marked `auto_publish` once it has earned
it; that is a decision someone makes, not the default.

**It never raises at the caller.** Same reasoning as accounts.services.stock:
this runs from cron and from a button on an admin screen, and a feed being
down, slow, or answering with an HTML error page is a normal Tuesday. Every
failure is recorded on the source row and the run continues to the next one.
"""
import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone as dt_timezone
from xml.etree import ElementTree

import requests
from django.utils import timezone

from .models import NewsItem, NewsSource
from accounts.moderation import ModerationStatus

log = logging.getLogger(__name__)

TIMEOUT = 15
# How many unreviewed stories from one source is too many to keep adding to.
BACKLOG_LIMIT = 25
# A default python-requests agent gets a challenge page from some publishers
# rather than XML — the same problem the share ticker hit.
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; APIS-Intranet/1.0)'}

# Atom and the media extensions, for feeds that use them.
NS = {
    'atom':  'http://www.w3.org/2005/Atom',
    'media': 'http://search.yahoo.com/mrss/',
    'dc':    'http://purl.org/dc/elements/1.1/',
}

TAG_RE = re.compile(r'<[^>]+>')
WS_RE = re.compile(r'\s+')


def _text(el):
    """Readable plain text from a feed field.

    Feed descriptions are routinely HTML — often a whole anchor tag wrapping
    the headline — and putting that on a card would either render as markup or
    show the tags. Entities are unescaped first so that '&amp;' does not
    survive into the summary.
    """
    if el is None:
        return ''
    raw = ''.join(el.itertext()) if len(el) else (el.text or '')
    import html
    raw = html.unescape(raw)
    return WS_RE.sub(' ', TAG_RE.sub(' ', raw)).strip()


def _parse_date(value):
    """A feed's date as a local date, or today's.

    Feeds are inconsistent enough here that guessing wrong is normal; today is
    a safe answer because the strip sorts by this and an unparseable date
    should not send a story to 1970 or to next year.
    """
    if not value:
        return timezone.localdate()
    value = value.strip()
    for fmt in ('%a, %d %b %Y %H:%M:%S %z', '%a, %d %b %Y %H:%M:%S %Z',
                '%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d'):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=dt_timezone.utc)
            return timezone.localtime(dt).date()
        except ValueError:
            continue
    log.info('news feed: unreadable date %r', value[:60])
    return timezone.localdate()


def _image_for(item):
    """A thumbnail, if the feed offers one in a form we can trust.

    Most will not — Google News gives none. That is fine: the card has a real
    no-picture state, so an absent image is a normal outcome and not a gap to
    be filled by scraping the article page, which would mean fetching an
    arbitrary URL per story on a schedule.
    """
    for path in ('media:thumbnail', 'media:content'):
        el = item.find(path, NS)
        if el is not None and el.get('url', '').startswith('http'):
            return el.get('url')
    enc = item.find('enclosure')
    if enc is not None:
        url = enc.get('url', '')
        if url.startswith('http') and (enc.get('type', '')).startswith('image/'):
            return url
    return ''


def _entries(xml_text):
    """Feed items as plain dicts, RSS or Atom.

    Returns [] rather than raising on anything unparseable: a publisher
    answering with an HTML error page is the common case, and it is not worth
    a traceback.
    """
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as e:
        log.warning('news feed: not XML (%s)', e)
        return []

    out = []
    # RSS 2.0
    for item in root.findall('.//item'):
        link = _text(item.find('link'))
        out.append({
            'title': _text(item.find('title')),
            'summary': _text(item.find('description')),
            'link': link,
            'guid': _text(item.find('guid')) or link,
            'date': _parse_date(_text(item.find('pubDate'))),
            'image': _image_for(item),
            'source': (_text(item.find('source'))
                       or _text(item.find('dc:creator', NS))),
        })
    if out:
        return out

    # Atom
    for entry in root.findall('atom:entry', NS):
        link_el = entry.find('atom:link', NS)
        link = (link_el.get('href') if link_el is not None else '') or ''
        out.append({
            'title': _text(entry.find('atom:title', NS)),
            'summary': (_text(entry.find('atom:summary', NS))
                        or _text(entry.find('atom:content', NS))),
            'link': link,
            'guid': _text(entry.find('atom:id', NS)) or link,
            'date': _parse_date(_text(entry.find('atom:updated', NS))
                                or _text(entry.find('atom:published', NS))),
            'image': '',
            'source': _text(entry.find('atom:author/atom:name', NS)),
        })
    return out


def _key(value):
    """The dedupe key for a feed item: a hash, because the raw id is too long
    to index. See NewsItem.external_id."""
    return hashlib.sha256((value or '').encode('utf-8', 'replace')).hexdigest()


def _tidy_summary(summary, title, publisher):
    """The blurb, or nothing at all if it only repeats the headline.

    Google News sets every description to the headline followed by the
    publisher, so a card drawn straight from the feed says the same sentence
    twice in two sizes. Better to show a headline alone than a headline and its
    own echo, so anything that adds nothing is dropped and the card lays itself
    out without a blurb.
    """
    def norm(v):
        return re.sub(r'[^a-z0-9]+', ' ', (v or '').lower()).strip()

    n_sum, n_title = norm(summary), norm(title)
    if not n_sum or not n_title:
        return ''
    if n_sum == n_title:
        return ''
    if n_sum.startswith(n_title):
        rest = n_sum[len(n_title):].strip()
        # What is left is usually just the publisher's name, or nothing.
        if not rest or rest == norm(publisher) or len(rest) < 10:
            return ''
    # Anything else is a different sentence and is kept. A blunter rule -- drop
    # whatever is not much longer than the headline -- also throws away a real
    # one-line blurb, which is worth more than the echoes it would catch.
    return summary


def _tidy_title(title, feed_source):
    """Google News appends ' - The Publisher' to every headline. On a card
    that already shows the publisher underneath, it is the same words twice."""
    if feed_source and title.endswith(f' - {feed_source}'):
        return title[: -len(f' - {feed_source}')].strip()
    return title


def fetch_source(source):
    """Fetch one source. Returns (found, added, error) and never raises.

    The source row is updated with what happened either way, so a feed that has
    quietly stopped working shows it on the screen that manages it rather than
    only in a log nobody opens.
    """
    found = added = 0
    error = ''

    # If nobody has reviewed this feed's last few batches, stop adding to the
    # pile. Five a day against a queue nobody opens is how a review screen
    # becomes three hundred rows and stops being used at all; the feed is not
    # going anywhere, and what is still there tomorrow is what is still worth
    # carrying. Checked before the network call -- there is no point fetching.
    #
    # Recorded as 'paused', not 'failed'. Nothing is broken, and a screen that
    # calls this a failure sends somebody looking for a fault that is not there.
    waiting = NewsItem.objects.filter(
        source_ref=source, moderation_status=ModerationStatus.PENDING).count()
    if waiting >= BACKLOG_LIMIT:
        source.last_status = 'paused'
        source.last_error = (f'{waiting} stories from this source are waiting for '
                             f'approval. Review or clear those and it resumes.')
        source.last_fetched_at = timezone.now()
        source.last_found = 0
        source.last_added = 0
        source.save(update_fields=['last_fetched_at', 'last_status', 'last_error',
                                   'last_found', 'last_added'])
        return 0, 0, ''

    try:
        url = source.resolved_url()
        if not url:
            raise ValueError('This source has no address to fetch.')
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            raise ValueError(f'The feed answered HTTP {r.status_code}.')

        entries = _entries(r.text)
        found = len(entries)

        # Newest first, so a capped run takes the latest rather than whatever
        # order the publisher happened to serve.
        entries.sort(key=lambda e: e['date'], reverse=True)
        oldest_wanted = timezone.localdate() - timedelta(days=source.max_age_days)

        for e in entries:
            if added >= source.max_per_run:
                break
            title, link = e['title'].strip(), e['link'].strip()
            if not title or not link.startswith('http'):
                continue
            # Judged on the FINAL headline, not the raw one. Google appends
            # " - The Publisher" to every title, so a placeholder row arrives as
            # "BlogDescription - PIB" -- which has spaces and sails past a check
            # made too early, then loses the suffix and becomes a card reading
            # "BlogDescription".
            publisher = e['source'] or ''
            title = _tidy_title(title, publisher)
            if len(title) < 12 or ' ' not in title:
                continue
            # See NewsSource.max_age_days: without this the job walks backwards
            # through the feed's archive five stories a day.
            if e['date'] < oldest_wanted:
                continue

            # Two keys because feeds re-publish: the feed's own id, and the
            # link. Either one matching means we have already carried it.
            #
            # The id is hashed rather than stored: Google's run past 800
            # characters and the column is indexed. Nothing here is truncated
            # any more -- a clipped link is a broken link, and a clipped key
            # matches nothing, so every run re-carried the same stories.
            key = _key(e['guid'] or link)
            if NewsItem.objects.filter(external_id=key).exists():
                continue
            if NewsItem.objects.filter(source_url=link).exists():
                continue

            summary = _tidy_summary(e['summary'], title, publisher)
            n = NewsItem(
                title=title[:200],
                summary=summary[:600],
                category=source.category,
                source_name=publisher[:120],
                source_url=link,
                image_url=(e['image'] or '')[:2000],
                published_on=e['date'],
                source_ref=source,
                external_id=key,
            )
            if source.auto_publish:
                n.moderation_status = ModerationStatus.APPROVED
                n.review_note = f'Published automatically from {source.name}.'
                n.reviewed_at = timezone.now()
            # else: left PENDING by ModeratedContent's own default.
            n.save()
            added += 1

        source.last_status = 'ok'
        source.last_error = ''
    except Exception as e:
        # Total on purpose — see the module docstring.
        error = str(e)[:300]
        source.last_status = 'failed'
        source.last_error = error
        log.warning('news feed %s: %s', source.name, error)

    source.last_fetched_at = timezone.now()
    source.last_found = min(found, 32767)
    source.last_added = min(added, 32767)
    source.save(update_fields=['last_fetched_at', 'last_status', 'last_error',
                               'last_found', 'last_added'])
    return found, added, error


def purge_old():
    """Delete fetched stories past NewsItem.PURGE_AFTER_DAYS.

    Nobody is going to look up what a feed carried three months ago, and a
    table that only grows is a table that eventually needs explaining. Pinned
    stories stay, and so does anything written by hand -- that is somebody's
    own work and no feed can bring it back.
    """
    cutoff = timezone.localdate() - timedelta(days=NewsItem.PURGE_AFTER_DAYS)
    gone, _ = (NewsItem.objects
               .filter(source_ref__isnull=False, pinned=False,
                       published_on__lt=cutoff)
               .delete())
    return gone


def fetch_all():
    """Every active source. Returns a per-source summary for the caller to
    print or show, and does not stop at the first failure."""
    results = []
    for source in NewsSource.objects.filter(is_active=True):
        found, added, error = fetch_source(source)
        results.append({'source': source.name, 'found': found,
                        'added': added, 'error': error})
    purge_old()
    return results
