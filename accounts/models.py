"""The identity layer for the whole intranet.

Every tool on this server used to be reachable by anyone who knew its URL —
each one carried its own idea of who a user was (TA/DA imported its own
directory from Excel, PMS had another). This app puts a single door in front
of all of them: you sign in once, with your work email and a one-time code,
and the portal decides which tools you see.

Employee identity is not owned here. It is synced from Pocket HRMS, which is
already the company's hire-to-retire system of record — so a joiner, a
transfer or an exit is reflected here without anyone re-uploading a sheet.
What Pocket HRMS does NOT know about is access: who is a superadmin, which
tools a person may open. Those are the portal's own columns and survive every
sync (see HrmsSyncService for exactly which fields a sync is allowed to
touch).
"""
import hashlib
import secrets
from datetime import timedelta

from django.db import models
from django.utils import timezone


# The one account that can always get in, even on an empty database, and can
# never be locked out of its own console — bootstrapped on first sign-in so
# there is no chicken-and-egg between "sync employees" and "have an admin".
SUPERADMIN_BOOTSTRAP_EMAIL = 'anshul@apisindia.com'


class AppKey(models.TextChoices):
    """Every tool the portal can grant access to.

    Kept as a choices list rather than a free-string so a typo in the admin
    console cannot silently grant access to a tool that does not exist. The
    values match the frontend's ShellView ids.
    """
    HOME           = 'home',           'Dashboard'
    EXTRACTOR      = 'extractor',      'Data Extractor'
    PERFORMANCE    = 'performance',    'Performance Hub'
    APPRAISAL      = 'appraisal',      'Appraisal Hub'
    EOM            = 'eom',            'EOM Hub'
    PMS            = 'pms',            'PMS Simulator'
    OFFER_LETTERS  = 'offer-letters',  'Letters Generator'
    ROOMPULSE      = 'roompulse',      'AdminPulse'
    SALESIQ        = 'salesiq',        'SalesIQ'
    TADA           = 'tada',           'TA/DA Portal'
    APIS_TREE      = 'apis-tree',      'APIS Tree'
    POLICIES       = 'policies',       'Policies'


# What a brand-new employee can open before anyone grants them more. The
# dashboard and the read-only reference pages only — never a tool that moves
# money or writes records.
DEFAULT_APPS = [AppKey.HOME, AppKey.APIS_TREE, AppKey.POLICIES]


class PortalUser(models.Model):
    """One person who may sign in to the intranet.

    The email is the login identity rather than the employee code: people know
    their own email, type it correctly, and it is what the one-time code is
    sent to. The code is kept as the join key back to Pocket HRMS.
    """

    # ── Identity, synced from Pocket HRMS ────────────────────────────────────
    employee_code = models.CharField(max_length=50, unique=True)
    email         = models.EmailField(unique=True)
    name          = models.CharField(max_length=200)
    designation   = models.CharField(max_length=200, blank=True)
    department    = models.CharField(max_length=200, blank=True)
    location      = models.CharField(max_length=200, blank=True)
    reporting_manager_code = models.CharField(max_length=50, blank=True)

    # HRMS's own row id, kept so a sync can match a record whose code was
    # corrected upstream without creating a duplicate person.
    hrms_id       = models.CharField(max_length=50, blank=True, db_index=True)

    # ── Access, owned by the portal and never overwritten by a sync ──────────
    is_active     = models.BooleanField(default=True)
    is_superadmin = models.BooleanField(default=False)
    # Which tools this person may open. A superadmin bypasses this entirely.
    app_access    = models.JSONField(default=list, blank=True)

    # The last record Pocket HRMS returned for this person, verbatim. Kept so
    # the console can show exactly what upstream is sending - including fields
    # the portal does not itself use - rather than only the handful mapped
    # into columns above. Only the identity fields in
    # HrmsSyncService.EMPLOYEE_FIELDS are ever requested, so this deliberately
    # never contains payroll or password data.
    hrms_raw = models.JSONField(default=dict, blank=True)

    # ── Bookkeeping ─────────────────────────────────────────────────────────
    # False for a row created by hand in the console before HRMS knows about
    # them (a contractor, a new joiner mid-cycle) - a sync will adopt the row
    # once a matching code appears rather than duplicating it.
    from_hrms      = models.BooleanField(default=False)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_login_at  = models.DateTimeField(null=True, blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} <{self.email}>"

    @property
    def is_bootstrap_superadmin(self):
        """The founding account, which the console refuses to demote or disable.

        Without this an admin could remove their own access - or someone
        else's last remaining access - and lock the company out of its own
        portal with no way back in short of a database edit.
        """
        return self.email.lower() == SUPERADMIN_BOOTSTRAP_EMAIL.lower()

    def can_open(self, app_key):
        """May this user open a given tool?"""
        if not self.is_active:
            return False
        if self.is_superadmin:
            return True
        return str(app_key) in (self.app_access or [])

    @property
    def allowed_apps(self):
        """Every tool this user may open, superadmin included."""
        if self.is_superadmin:
            return [c.value for c in AppKey]
        return [a for a in (self.app_access or []) if a in AppKey.values]


class PortalOTP(models.Model):
    """A one-time sign-in code.

    The code is stored hashed. A six-digit number is small enough that a leaked
    database table would otherwise hand over live sign-in codes, and the portal
    never needs to read one back - only to compare.
    """
    RESEND_WINDOW   = timedelta(minutes=15)
    MAX_PER_WINDOW  = 4         # resend attempts allowed inside that window
    MAX_ATTEMPTS    = 5         # wrong guesses before the code dies
    TTL             = timedelta(minutes=5)

    user       = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name='otps')
    code_hash  = models.CharField(max_length=64)
    attempts   = models.PositiveIntegerField(default=0)
    is_used    = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @staticmethod
    def hash_code(code):
        return hashlib.sha256(str(code).encode()).hexdigest()

    @classmethod
    def issue(cls, user):
        """Mint a fresh code, retiring any the user already had."""
        cls.objects.filter(user=user, is_used=False).update(is_used=True)
        code = f"{secrets.randbelow(1_000_000):06d}"
        cls.objects.create(user=user, code_hash=cls.hash_code(code),
                           expires_at=timezone.now() + cls.TTL)
        return code

    @property
    def is_live(self):
        return (not self.is_used
                and self.attempts < self.MAX_ATTEMPTS
                and self.expires_at > timezone.now())

    def matches(self, code):
        """Compare in constant time, and burn an attempt either way."""
        self.attempts += 1
        self.save(update_fields=['attempts'])
        return secrets.compare_digest(self.code_hash, self.hash_code(code))


class PortalSession(models.Model):
    """A signed-in browser.

    Stored as a hash of the token for the same reason as the OTP: whoever can
    read this table should not thereby be able to impersonate everyone in it.
    Sessions are listed and revocable from the admin console, so losing a
    laptop is a two-click problem rather than a password reset for the company.
    """
    TTL = timedelta(hours=12)

    user        = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name='sessions')
    token_hash  = models.CharField(max_length=64, unique=True, db_index=True)
    user_agent  = models.CharField(max_length=300, blank=True)
    ip_address  = models.CharField(max_length=64, blank=True)
    revoked_at  = models.DateTimeField(null=True, blank=True)
    expires_at  = models.DateTimeField()
    created_at  = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-last_seen_at']

    @staticmethod
    def hash_token(token):
        return hashlib.sha256(str(token).encode()).hexdigest()

    @classmethod
    def start(cls, user, user_agent='', ip=''):
        """Open a session and hand back the raw token - the only time it exists."""
        token = secrets.token_urlsafe(32)
        cls.objects.create(user=user, token_hash=cls.hash_token(token),
                           user_agent=(user_agent or '')[:300], ip_address=(ip or '')[:64],
                           expires_at=timezone.now() + cls.TTL)
        return token

    @property
    def is_live(self):
        return self.revoked_at is None and self.expires_at > timezone.now()

    def touch(self):
        """Slide the window forward so an active user is not logged out mid-task."""
        now = timezone.now()
        self.last_seen_at = now
        self.expires_at = now + self.TTL
        self.save(update_fields=['last_seen_at', 'expires_at'])


class HrmsSyncLog(models.Model):
    """What a sync did, so an unexpected change to the directory is traceable.

    Deliberately records the counts and the error rather than the payload:
    employee master data is not something to keep a second copy of in a log
    table.
    """
    started_at  = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    triggered_by = models.CharField(max_length=200, blank=True)   # email, or 'schedule'
    ok          = models.BooleanField(default=False)
    fetched     = models.PositiveIntegerField(default=0)
    created     = models.PositiveIntegerField(default=0)
    updated     = models.PositiveIntegerField(default=0)
    deactivated = models.PositiveIntegerField(default=0)
    skipped_no_email = models.PositiveIntegerField(default=0)
    message     = models.TextField(blank=True)

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f"HRMS sync {self.started_at:%Y-%m-%d %H:%M} ({'ok' if self.ok else 'failed'})"
