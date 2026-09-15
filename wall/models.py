"""APIS Wall — the company photo wall, with an approval gate in front of it.

The wall shipped as a frontend-only page: "Upload Image" turned the chosen
file into a browser object URL, so the photo was visible to the person who
picked it, to nobody else, and only until they reloaded. Nothing was stored
and nothing was reviewed.

Photos are different from the other content here in one way that matters:
they are opaque. A vacancy with a wrong field is visibly wrong in the
approval queue, but a photograph has to actually be looked at before it goes
on the company's wall — which is the whole reason this gate exists.
"""
import os
import uuid

from django.db import models

from accounts.moderation import ModeratedContent, ModerationStatus


def photo_path(instance, filename):
    """Store under a generated name, keeping only the extension.

    Uploaded filenames are attacker-controlled and routinely carry names like
    'IMG_2024 (1).jpeg' or worse; a uuid sidesteps both the path-traversal
    question and collisions between two people uploading 'photo.jpg'.
    """
    ext = os.path.splitext(filename or '')[1].lower()[:10] or '.jpg'
    return f'wall/{uuid.uuid4().hex}{ext}'


class WallPhoto(ModeratedContent):
    """One photo on the wall.

    Seed photos that ship in the frontend's public/Apis_wall/ folder are not
    represented here — they are part of the build, not user content, and
    nobody uploaded them. The page shows those first and these after.
    """

    CATEGORY_CHOICES = [
        ('Celebrations', 'Celebrations'),
        ('Team Moments', 'Team Moments'),
        ('CSR', 'CSR'),
        ('Events', 'Events'),
        ('Other', 'Other'),
    ]

    title    = models.CharField(max_length=200)
    category = models.CharField(max_length=40, choices=CATEGORY_CHOICES, default='Other')
    image    = models.ImageField(upload_to=photo_path)
    caption  = models.CharField(max_length=300, blank=True)

    # Original filename and size, kept for the console — an administrator
    # reviewing an upload should be able to see what was actually sent.
    original_name = models.CharField(max_length=255, blank=True)
    size_bytes    = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'wall photo'

    def __str__(self):
        return f'{self.title} ({self.category})'

    def moderation_label(self):
        return self.title

    def moderation_preview(self, request=None):
        """The photo itself — the only thing worth reviewing here."""
        if not self.image:
            return ''
        url = self.image.url
        return request.build_absolute_uri(url) if request is not None else url

    def moderation_detail(self):
        return {
            'Category': self.category,
            'Caption': self.caption,
            'File': self.original_name,
            'Size': f'{self.size_bytes / 1024:.0f} KB' if self.size_bytes else '',
        }

    def delete(self, *args, **kwargs):
        """Take the file with the row.

        Without this a rejected or removed photo stays on disk indefinitely —
        still served by its media URL to anyone who noted it down, which
        defeats the point of removing it.
        """
        image = self.image
        super().delete(*args, **kwargs)
        if image:
            image.storage.delete(image.name)

    @classmethod
    def published(cls):
        return cls.objects.filter(moderation_status=ModerationStatus.APPROVED)
