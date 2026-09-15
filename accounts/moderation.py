"""One approval gate and one audit trail, shared by everything on the dashboard.

The dashboard grew a habit: a tool would let anyone POST a row and would then
show that row to the whole company immediately. A vacancy, a wall photo, a
referral — each app re-invented "create and publish" and none of them recorded
who did it. On a server reachable from the internet that means anything on the
company's home page could have been put there by anyone, and nobody could say
by whom.

Everything user-submitted now inherits ModeratedContent and starts life
PENDING. Nothing reaches the dashboard until a superadmin approves it, and
every create/approve/reject/edit/delete is written to ActivityLog.

Two deliberate choices:

* A superadmin's own submission is auto-approved. They are the approving
  authority — making them queue work for themselves adds a step without adding
  control, and the activity log still records that they created it.
* Names and emails are snapshotted onto the row alongside the foreign key. A
  person can leave and be deactivated, and an HRMS sync can change what their
  record says; the audit trail has to keep saying what was true at the time.
"""
from django.db import models
from django.utils import timezone


class ModerationStatus(models.TextChoices):
    PENDING  = 'pending',  'Pending approval'
    APPROVED = 'approved', 'Approved'
    REJECTED = 'rejected', 'Rejected'


class ModeratedContent(models.Model):
    """Abstract base for anything a user can put on the dashboard.

    Inheritors get the approval state, who submitted it, and who reviewed it.
    Use the `published` manager method rather than filtering by hand so no
    view can accidentally leak a pending row.
    """

    moderation_status = models.CharField(
        max_length=10, choices=ModerationStatus.choices,
        default=ModerationStatus.PENDING, db_index=True)

    submitted_by = models.ForeignKey(
        'accounts.PortalUser', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='%(app_label)s_%(class)s_submissions')
    # Snapshots — see the module docstring.
    submitted_by_name  = models.CharField(max_length=200, blank=True)
    submitted_by_email = models.CharField(max_length=254, blank=True)

    reviewed_by = models.ForeignKey(
        'accounts.PortalUser', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='%(app_label)s_%(class)s_reviews')
    reviewed_by_name = models.CharField(max_length=200, blank=True)
    reviewed_at      = models.DateTimeField(null=True, blank=True)
    # Why it was rejected, shown back to the person who submitted it.
    review_note      = models.CharField(max_length=500, blank=True)

    class Meta:
        abstract = True

    # ── state ───────────────────────────────────────────────────────────────
    @property
    def is_published(self):
        return self.moderation_status == ModerationStatus.APPROVED

    def attribute_to(self, user):
        """Stamp the submitter onto a row being created."""
        self.submitted_by = user
        self.submitted_by_name = user.name if user else ''
        self.submitted_by_email = user.email if user else ''

    def set_review(self, reviewer, decision, note=''):
        """Record an approve/reject. Does not save — the caller does, so it
        can batch the write with whatever else it is changing."""
        self.moderation_status = decision
        self.reviewed_by = reviewer
        self.reviewed_by_name = reviewer.name if reviewer else ''
        self.reviewed_at = timezone.now()
        self.review_note = (note or '')[:500]

    # What the console shows in the pending queue. Each inheritor describes
    # itself rather than the console knowing the shape of every model.
    def moderation_label(self):
        return str(self)

    def moderation_detail(self):
        return {}

    def moderation_payload(self):
        """The common envelope every queue row carries."""
        return {
            'id': self.id,
            'type': self._meta.model_name,
            'type_label': self._meta.verbose_name.title(),
            'label': self.moderation_label(),
            'detail': self.moderation_detail(),
            'status': self.moderation_status,
            'submitted_by': self.submitted_by_name or 'Unknown',
            'submitted_by_email': self.submitted_by_email,
            'submitted_at': self.created_at.isoformat() if getattr(self, 'created_at', None) else None,
            'reviewed_by': self.reviewed_by_name,
            'reviewed_at': self.reviewed_at.isoformat() if self.reviewed_at else None,
            'review_note': self.review_note,
        }


def log_activity(actor, action, obj=None, summary='', detail=None, request=None,
                 object_type='', object_id=None):
    """Write one line to the audit trail.

    Never raises: an audit write failing must not take down the action it was
    recording, and a lost log line is a smaller problem than a 500 on a form
    the user filled in correctly. Import locally to keep this module free of
    an import cycle with models.py.
    """
    from .auth import client_ip
    from .models import ActivityLog

    try:
        if obj is not None and not object_type:
            object_type = obj._meta.model_name
        if obj is not None and object_id is None:
            object_id = obj.pk

        ActivityLog.objects.create(
            actor=actor,
            actor_name=(actor.name if actor else 'System'),
            actor_email=(actor.email if actor else ''),
            action=action,
            object_type=object_type or '',
            object_id=object_id,
            summary=(summary or '')[:300],
            detail=detail or {},
            ip_address=client_ip(request) if request is not None else '',
        )
    except Exception:
        pass
