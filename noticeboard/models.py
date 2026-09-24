"""Dashboard content that HR maintains, rather than a developer.

Both of these lived in the frontend as hard-coded arrays. That meant the
announcements card showed the same two placeholder rows to the whole company
for as long as nobody shipped a build, and next year's holiday list would need
a code change and a deploy to appear.

They are different in one way that decides how each is governed:

* An announcement is a claim made to the company. Anyone may propose one, and
  it goes through the approval queue like every other piece of dashboard
  content.
* A holiday list is a transcription of a signed HR circular. It is not
  something to crowd-source — only an administrator writes it, so there is
  nothing to queue and no ModeratedContent on it. What it does carry is the
  same audit trail: every edit is logged with who made it.
"""
import os
import uuid

from django.db import models
from django.utils import timezone

from accounts.moderation import ModeratedContent, ModerationStatus


class Announcement(ModeratedContent):
    """One notice on the dashboard's Announcements card."""

    # Matches the icon the card already draws per announcement. Kept as a
    # short key rather than a class name so the backend never has an opinion
    # about which icon set the frontend uses.
    TONE_CHOICES = [
        ('general',     'General'),
        ('maintenance', 'Maintenance / downtime'),
        ('update',      'Product or system update'),
        ('celebration', 'Celebration'),
        ('urgent',      'Urgent'),
    ]

    title = models.CharField(max_length=200)
    body  = models.TextField(max_length=2000)
    tone  = models.CharField(max_length=20, choices=TONE_CHOICES, default='general')

    # The date the notice is *about*, which is not always the day it was
    # written — a maintenance window announced a week ahead reads wrongly if
    # the card shows the day someone typed it.
    announced_on = models.DateField(default=timezone.localdate)
    # After this the card stops showing it. Blank means it stays until taken
    # down by hand: most notices are worth expiring, a policy change is not.
    expires_on   = models.DateField(null=True, blank=True)
    # Holds a notice at the top regardless of date.
    pinned       = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-pinned', '-announced_on', '-created_at']

    def __str__(self):
        return self.title

    def moderation_label(self):
        return self.title

    def moderation_detail(self):
        return {
            'Type': self.get_tone_display(),
            'Says': self.body[:200] + ('…' if len(self.body) > 200 else ''),
            'Dated': self.announced_on.strftime('%d %b %Y'),
            'Until': self.expires_on.strftime('%d %b %Y') if self.expires_on else 'No end date',
            'Pinned': 'Yes' if self.pinned else '',
        }

    @classmethod
    def published(cls):
        """Approved, and not past its end date.

        An expired notice is hidden rather than deleted — it stays in the
        console as a record of what the company was told and when.
        """
        today = timezone.localdate()
        return (cls.objects.filter(moderation_status=ModerationStatus.APPROVED)
                .filter(models.Q(expires_on__isnull=True) | models.Q(expires_on__gte=today)))


class HolidayZone(models.Model):
    """A group of locations sharing one holiday list.

    A separate table rather than a string on each holiday: the dashboard's
    zone picker needs the zones in a fixed order with their full labels, and
    thirteen rows repeating 'Andhra Pradesh / Telangana / Karnataka' is how
    one of them ends up spelled differently from the others.
    """

    key   = models.SlugField(max_length=40, unique=True)
    label = models.CharField(max_length=120)
    # Explicit rather than alphabetical: the circular lists head office first,
    # then the plant, then the rest — and the picker should match it.
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'label']

    def __str__(self):
        return self.label


class Holiday(models.Model):
    """One holiday in one zone, transcribed from the signed HR circular."""

    TYPE_CHOICES = [('National', 'National'), ('State', 'State')]

    zone = models.ForeignKey(HolidayZone, on_delete=models.CASCADE, related_name='holidays')
    date = models.DateField()
    name = models.CharField(max_length=120)
    type = models.CharField(max_length=10, choices=TYPE_CHOICES, default='State')

    class Meta:
        ordering = ['date']
        # The same zone cannot list the same day twice. Re-running a seed or
        # pasting a list in twice is the normal way duplicates arrive.
        constraints = [
            models.UniqueConstraint(fields=['zone', 'date', 'name'],
                                    name='one_holiday_per_zone_day'),
        ]

    def __str__(self):
        return f'{self.name} — {self.date} ({self.zone.key})'


def news_image_path(instance, filename):
    """Same reasoning as wall.models.photo_path — uploaded names are not to be
    trusted, and two curators will eventually both upload 'news.jpg'."""
    ext = os.path.splitext(filename or '')[1].lower()[:10] or '.jpg'
    return f'news/{uuid.uuid4().hex}{ext}'


class NewsItem(ModeratedContent):
    """One story on the dashboard's Daily News strip.

    This is news about the world the company sells into — APIs, food APIs,
    nutraceuticals, honey — and about APIS itself. It is not news about the
    intranet's own tools; that is the frontend's WHATS_NEW array, which is
    developer-written and ships with a build.

    Governed like an Announcement rather than like a Holiday: a holiday list
    is a transcription only HR can make, but a useful article is most often
    spotted by whoever happens to read the trade press. Anyone signed in may
    put one forward, nothing appears until an administrator approves it, and
    an administrator's own post is approved on the way in.

    The failure mode of a news strip is not a wrong story, it is three-month-old
    stories sitting under the words "latest updates". So the age of each item
    is carried to the card and shown there, and `stale_after_days` gives the
    console something to warn the curator with.
    """

    CATEGORY_CHOICES = [
        ('company',        'Company'),
        ('apis',           'APIs'),
        ('food_apis',      'Food APIs'),
        ('nutraceuticals', 'Nutraceuticals'),
        ('industry',       'Industry'),
        ('products',       'Products'),
    ]

    # A strip headed "latest updates" that has not moved in a fortnight is
    # worse than no strip. This is what the console measures itself against.
    STALE_AFTER_DAYS = 14

    title    = models.CharField(max_length=200)
    summary  = models.TextField(max_length=600)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='industry')

    # Where it came from. Both optional: a company post has no outside source,
    # and a curator may have the headline but not a link worth sending people to.
    source_name = models.CharField(max_length=120, blank=True)
    source_url  = models.URLField(max_length=500, blank=True)

    # Either an uploaded file or somebody else's URL. Two fields rather than
    # one because they fail differently: an upload is ours and keeps working,
    # a remote URL can rot or be blocked at any time, and the card has to be
    # able to fall back to no picture at all without looking broken.
    image     = models.ImageField(upload_to=news_image_path, null=True, blank=True)
    image_url = models.URLField(max_length=500, blank=True)

    # The day the story is *about*. An article found late still belongs on its
    # own date, or the strip claims a week-old piece broke this morning.
    published_on = models.DateField(default=timezone.localdate)
    expires_on   = models.DateField(null=True, blank=True)
    pinned       = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-pinned', '-published_on', '-created_at']

    def __str__(self):
        return self.title

    def moderation_label(self):
        return self.title

    def moderation_detail(self):
        return {
            'Category': self.get_category_display(),
            'Says': self.summary[:200] + ('…' if len(self.summary) > 200 else ''),
            'Source': self.source_name or (self.source_url or 'No source given'),
            'Dated': self.published_on.strftime('%d %b %Y'),
            'Pinned': 'Yes' if self.pinned else '',
        }

    @classmethod
    def published(cls):
        today = timezone.localdate()
        return (cls.objects.filter(moderation_status=ModerationStatus.APPROVED)
                .filter(models.Q(expires_on__isnull=True) | models.Q(expires_on__gte=today)))
