"""Policies & Guidelines — the company's document register.

The page shipped frontend-only: "Add Policy" turned the chosen PDF into a
browser object URL, so the document existed for the person who picked it,
for nobody else, and only until they reloaded.

Five kinds of document live here — SOPs, manual policies, templates, work
instructions and formats — in one table, because "everything HR has filed
this year" should stay one query and a field added to one kind should not
have to be added to four others.

The 11 PDFs that ship in the frontend's public/Policies/ folder are not
represented here. They are part of the build, not anything a person
uploaded, and the page lists them alongside these.
"""
import os
import uuid

from django.db import models

from accounts.moderation import ModeratedContent, ModerationStatus


def document_path(instance, filename):
    """Store under a generated name, keeping only the extension — same
    reasoning as wall.models.photo_path. The original name is kept on the
    row for display."""
    ext = os.path.splitext(filename or '')[1].lower()[:10]
    return f'policies/{uuid.uuid4().hex}{ext}'


class PolicyDocument(ModeratedContent):
    """One document in the register."""

    # Values are the labels the page shows, as with WallPhoto: there is no
    # second spelling to keep in step.
    CATEGORY_CHOICES = [
        ('SOP', 'SOP'),
        ('Manual Policy', 'Manual Policy'),
        ('Templates', 'Templates'),
        ('Work Instructions', 'Work Instructions'),
        ('Formats', 'Formats'),
    ]

    title      = models.CharField(max_length=200)
    category   = models.CharField(max_length=40, choices=CATEGORY_CHOICES)
    department = models.CharField(max_length=60, blank=True)
    version    = models.PositiveSmallIntegerField(default=1)

    # Who signed the document off inside the business, as written on it.
    # Not the same thing as ModeratedContent.reviewed_by, which is the
    # superadmin who let it onto the intranet — hence the doc_ prefix.
    doc_approved_by = models.CharField(max_length=200, blank=True)
    doc_reviewed_by = models.CharField(max_length=200, blank=True)
    approval_date   = models.DateField(null=True, blank=True)

    file          = models.FileField(upload_to=document_path)
    original_name = models.CharField(max_length=255, blank=True)
    size_bytes    = models.PositiveIntegerField(default=0)
    # Read off the PDF at upload. Null for Word/Excel files, which have no
    # fixed page count until something renders them.
    pages         = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'policy document'

    def __str__(self):
        return f'{self.title} ({self.category})'

    def moderation_label(self):
        return self.title

    def moderation_detail(self):
        return {
            'Category': self.category,
            'Department': self.department,
            'Version': f'v{self.version}',
            'Approved by': self.doc_approved_by,
            'File': self.original_name,
            'Pages': str(self.pages) if self.pages else '',
        }

    def moderation_link(self, request=None):
        """The document itself. Its title says nothing about what it says,
        so the reviewer has to be able to open it. Root-relative, never
        build_absolute_uri — see roompulse/attachments.py url_for."""
        return self.file.url if self.file else ''

    def can_delete(self, user):
        """The uploader can take back their own upload — the "added it by
        accident" case — and a superadmin can remove anything."""
        if user is None:
            return False
        return user.is_superadmin or (self.submitted_by_id is not None
                                      and self.submitted_by_id == user.id)

    def delete(self, *args, **kwargs):
        """Take the file with the row, or it stays served by its media URL
        after the document is supposedly gone. See WallPhoto.delete."""
        f = self.file
        super().delete(*args, **kwargs)
        if f:
            f.storage.delete(f.name)

    @classmethod
    def published(cls):
        return cls.objects.filter(moderation_status=ModerationStatus.APPROVED)


class BuiltInRemoval(models.Model):
    """One of the PDFs that ship in the frontend's public/Policies/, taken
    off the page.

    Those files are part of the build, so nothing here can delete them; the
    page hides any whose filename is listed. Kept as a row rather than a
    flag so the register still says who took a policy down and when, and
    deleting the row (Django admin) puts it back.
    """

    file = models.CharField(max_length=255, unique=True)
    title = models.CharField(max_length=200, blank=True)
    removed_by = models.ForeignKey(
        'accounts.PortalUser', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+')
    removed_by_name = models.CharField(max_length=200, blank=True)
    removed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-removed_at']
        verbose_name = 'removed built-in policy'

    def __str__(self):
        return self.title or self.file
