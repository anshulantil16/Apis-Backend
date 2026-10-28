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
import os
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
    GOAL_SETTING   = 'goal-setting',   'Goal Setting Hub'
    EOM            = 'eom',            'EOM Hub'
    PMS            = 'pms',            'PMS Simulator'
    OFFER_LETTERS  = 'offer-letters',  'Letters Generator'
    HELPDESK       = 'helpdesk',       'Help Desk'
    SALESIQ        = 'salesiq',        'SalesIQ'
    TADA           = 'tada',           'TA/DA Portal'
    APIS_TREE      = 'apis-tree',      'APIS Tree'
    POLICIES       = 'policies',       'Policies'
    APIS_WALL      = 'apis-wall',      'APIS Wall'
    CTC            = 'ctc-calculator', 'CTC Calculator'


# What a brand-new employee can open before anyone grants them more. The
# dashboard and the read-only reference pages only — never a tool that moves
# money or writes records.
#
# Settable per environment, because the answer is not the same everywhere.
# A server opening to the whole company for the first time may want every
# new account to start with nothing at all, so that access is a decision
# somebody made rather than a default nobody chose.
#
#   PORTAL_DEFAULT_APPS=                  nobody starts with anything
#   PORTAL_DEFAULT_APPS=home,policies     just those two
#   (unset)                               the list below
#
# Unknown keys are dropped rather than stored: a typo in a server's .env
# must not put a grant in the database for a tool that does not exist.
def _default_apps():
    raw = os.getenv('PORTAL_DEFAULT_APPS')
    if raw is None:
        return [AppKey.HOME, AppKey.APIS_TREE, AppKey.POLICIES, AppKey.APIS_WALL]
    return [k for k in (p.strip() for p in raw.split(',')) if k in AppKey.values]


DEFAULT_APPS = _default_apps()


class PortalUser(models.Model):
    """One person who may sign in to the intranet.

    The email is the login identity rather than the employee code: people know
    their own email, type it correctly, and it is what the one-time code is
    sent to. The code is kept as the join key back to Pocket HRMS.
    """

    # ── Identity, synced from Pocket HRMS ────────────────────────────────────
    employee_code = models.CharField(max_length=50, unique=True)
    # NULL means "HRMS has no address for this person". They belong in the
    # directory -- they work here, they appear in headcount, departments and
    # reporting lines -- but they cannot sign in, because the one-time code
    # has nowhere to go. Six NA-coded staff and 47 others were being dropped
    # from the master entirely over this, which made the directory disagree
    # with the company. NULL rather than '' on purpose: MySQL allows many
    # NULLs under a unique index but only one empty string, so '' would let
    # exactly one person have no address and refuse the rest.
    email         = models.EmailField(unique=True, null=True, blank=True)
    name          = models.CharField(max_length=200)
    designation   = models.CharField(max_length=200, blank=True)
    department    = models.CharField(max_length=200, blank=True)
    location      = models.CharField(max_length=200, blank=True)
    # Who this person reports to. Pocket HRMS sends both in one string --
    # "(HO0235)Pankaj Tripathi" -- so they are split on the way in: the code
    # is what joins a person to their manager's record, the name is what a
    # screen shows, and the string holding both is no use for either. Empty
    # for everybody until their support populated the column on 15-10-2026.
    reporting_manager_code = models.CharField(max_length=50, blank=True)
    reporting_manager_name = models.CharField(max_length=200, blank=True)

    # The other three people on somebody's record, named by Pocket HRMS
    # support on 28-10-2026 as the columns HOD, LEAVERE and LEAVEAPP. Each
    # arrives the same way the reporting manager does -- one string holding
    # both parts, "(HO0235)Pankaj Tripathi" -- so each is split the same
    # way: the code joins to that person's record, the name is what a
    # screen shows.
    #
    # The head of department is not always the person somebody reports to,
    # and neither is always who signs off their leave, which is why these
    # are three separate pairs rather than one field reused.
    hod_code = models.CharField(max_length=50, blank=True)
    hod_name = models.CharField(max_length=200, blank=True)
    leave_recommender_code = models.CharField(max_length=50, blank=True)
    leave_recommender_name = models.CharField(max_length=200, blank=True)
    leave_approver_code = models.CharField(max_length=50, blank=True)
    leave_approver_name = models.CharField(max_length=200, blank=True)

    # Already requested from HRMS and already arriving on every row; they
    # were being dropped on the floor.
    #
    # `grade` is the cadre code -- "O2", "M3", "W1" -- which is the same
    # code pms.increment_group() looks for when working out an increment
    # band, and which is currently typed into a spreadsheet by hand. Having
    # it here does not change PMS; it means the number can be checked
    # against the company's own record instead of against a re-upload.
    #
    # 20 characters was sized for the codes above and was wrong: it is free
    # text in Pocket HRMS, and on 12-10-2026 somebody entered a grade that
    # did not fit. MySQL refused the write, which ended the whole sync and
    # froze the directory for 2,410 people. Widened to match `category`,
    # and hrms.py now also trims anything longer rather than failing.
    grade         = models.CharField(max_length=100, blank=True)
    # "Sales", "Factory", "Corporate" -- the broad grouping above
    # department, which HRMS calls Category.
    category      = models.CharField(max_length=100, blank=True)
    # The company-issued number. PersonalMobileNo is deliberately not stored:
    # a work directory is for reaching people at work.
    office_mobile = models.CharField(max_length=30, blank=True)

    # Birthdays and work anniversaries. Both are complete for every employee in
    # the HRMS feed, and both are only ever read a day-and-month at a time - the
    # year is kept because joining year is what makes an anniversary countable
    # ("5 years today"), not because anyone needs to see a date of birth in
    # full. Nothing renders the birth year; see the celebrations endpoint.
    date_of_birth   = models.DateField(null=True, blank=True)
    date_of_joining = models.DateField(null=True, blank=True)

    # HRMS's own row id, kept so a sync can match a record whose code was
    # corrected upstream without creating a duplicate person.
    hrms_id       = models.CharField(max_length=50, blank=True, db_index=True)

    # ── Access, owned by the portal and never overwritten by a sync ──────────
    is_active     = models.BooleanField(default=True)
    is_superadmin = models.BooleanField(default=False)
    # Which tools this person may open. A superadmin bypasses this entirely.
    app_access    = models.JSONField(default=list, blank=True)
    # Lets Super Admin delegate APIS Tree card edits (name, designation,
    # department, photo) to HR/managers without handing them the whole
    # console. A superadmin can always edit tree cards regardless of this.
    can_edit_tree = models.BooleanField(default=False)
    # And separately, the right to change who is ON the chart: add a person,
    # remove one, put somebody under a different HOD.
    #
    # Two grants rather than one because the two jobs are not the same size.
    # Correcting a misspelt designation is routine and wants delegating
    # widely; taking a person off the org chart is structural, visible to the
    # whole company, and wants one or two people. With a single flag, whoever
    # could fix a typo could also delete the managing director's card.
    #
    # Managing implies editing -- you cannot sensibly add a person and then
    # be unable to correct their title -- so require_tree_editor accepts
    # either (see accounts/auth.py).
    can_manage_tree = models.BooleanField(default=False)
    # Sees, and may add to, the confidential vacancies — positions being
    # filled quietly (a replacement nobody has been told about yet, a senior
    # hire). Granted per person by a superadmin; a superadmin always has it.
    can_view_confidential_vacancies = models.BooleanField(default=False)

    @property
    def sees_confidential_vacancies(self):
        return self.is_superadmin or self.can_view_confidential_vacancies

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
        return (self.email or '').lower() == SUPERADMIN_BOOTSTRAP_EMAIL.lower()

    def can_open(self, app_key):
        """May this user open a given tool?"""
        if not self.is_active:
            return False
        if self.is_superadmin:
            return True
        return str(app_key) in (self.app_access or [])

    @property
    def can_sign_in(self):
        """Whether this person can actually get in.

        The portal signs people in by emailing a one-time code, so no address
        means no way in -- however active the record is. In the directory for
        headcount and reporting, not in the portal.
        """
        return bool(self.is_active and self.email)

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


class AppSession(models.Model):
    """A signed-in browser for one of the standalone tools.

    Appraisal and EOM were built before the portal and kept their own OTP
    logins, which people outside the portal still use. Until now that login
    proved who you were once, at the door, and handed back a profile and
    nothing else: every request afterwards said who it was by putting an
    employee id in the URL, and the server believed it. A sweep on
    14-10-2026 found /api/appraisal/employees/ and /api/eom/employees/
    answering anyone at all -- names, employee ids, email addresses and
    phone numbers -- with no credential of any kind.

    This is the missing half of that login: it issues something only the
    person who passed the OTP holds. Deliberately the same shape as
    PortalSession, including storing only the hash, so that whoever can read
    this table cannot thereby become everyone in it.

    Keyed by app and employee id rather than a foreign key: Appraisal and
    EOM each have their own employee table, and one FK cannot point at both.
    """
    TTL = timedelta(hours=12)

    app          = models.CharField(max_length=20, db_index=True)   # 'appraisal' | 'eom'
    employee_id  = models.CharField(max_length=50, db_index=True)
    token_hash   = models.CharField(max_length=64, unique=True, db_index=True)
    user_agent   = models.CharField(max_length=300, blank=True)
    ip_address   = models.CharField(max_length=64, blank=True)
    revoked_at   = models.DateTimeField(null=True, blank=True)
    expires_at   = models.DateTimeField()
    created_at   = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-last_seen_at']

    def __str__(self):
        return f'{self.app}:{self.employee_id}'

    @staticmethod
    def hash_token(token):
        return hashlib.sha256(str(token).encode()).hexdigest()

    @classmethod
    def start(cls, app, employee_id, user_agent='', ip=''):
        """Open a session and hand back the raw token - the only time it exists."""
        token = secrets.token_urlsafe(32)
        cls.objects.create(app=app, employee_id=str(employee_id),
                           token_hash=cls.hash_token(token),
                           user_agent=(user_agent or '')[:300], ip_address=(ip or '')[:64],
                           expires_at=timezone.now() + cls.TTL)
        return token

    @property
    def is_live(self):
        return self.revoked_at is None and self.expires_at > timezone.now()

    def touch(self):
        """Slide the window forward so an active user is not logged out
        halfway through a goal sheet."""
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
    # People HRMS has no address for. They ARE imported -- the directory
    # should match the company -- but they cannot sign in until HR adds
    # one, so the count is shown to say who is waiting on that.
    skipped_no_email = models.PositiveIntegerField(default=0)
    # Ex-employees in the feed who never had a portal account. Counted rather
    # than imported - see hrms._write_employees.
    skipped_leavers = models.PositiveIntegerField(default=0)
    # Rows whose email was already taken by an earlier employee in the same
    # run. Skipped rather than allowed to overwrite; the codes are named in
    # `message` so the clash can be fixed upstream.
    skipped_duplicate_email = models.PositiveIntegerField(default=0)
    # Rows the database itself refused -- a value that will not fit, a
    # constraint upstream data does not respect. One of these used to end
    # the run and roll back every other row; they are now skipped, counted
    # and named. See hrms._write_employees.
    skipped_bad_row = models.PositiveIntegerField(default=0)
    message     = models.TextField(blank=True)

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f"HRMS sync {self.started_at:%Y-%m-%d %H:%M} ({'ok' if self.ok else 'failed'})"


class ActivityLog(models.Model):
    """Append-only record of who did what on the intranet.

    The dashboard shows company-wide content — vacancies, wall photos,
    announcements — and until now none of it was attributable: rows appeared
    with a timestamp and no author. This is the answer to "who put that
    there", and it is written for every create, approve, reject, edit and
    delete that passes through the moderation gate.

    Nothing deletes from this table. A row here is evidence, and evidence that
    can be tidied away by whoever is being audited is not evidence — the
    console offers filtering and paging, never a delete.
    """

    class Action(models.TextChoices):
        CREATED   = 'created',   'Created'
        APPROVED  = 'approved',  'Approved'
        REJECTED  = 'rejected',  'Rejected'
        EDITED    = 'edited',    'Edited'
        DELETED   = 'deleted',   'Deleted'
        PUBLISHED = 'published', 'Published'
        HIDDEN    = 'hidden',    'Hidden'

    # Kept nullable so deactivating a leaver never erases what they did; the
    # name/email snapshots below are what the console actually displays.
    actor       = models.ForeignKey(PortalUser, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='activity')
    actor_name  = models.CharField(max_length=200, blank=True)
    actor_email = models.CharField(max_length=254, blank=True)

    action      = models.CharField(max_length=20, choices=Action.choices, db_index=True)
    # Model name ('vacancy', 'wallphoto'), not a ContentType FK — the log
    # outlives the models it describes, and a deleted row should still read
    # sensibly years later.
    object_type = models.CharField(max_length=50, blank=True, db_index=True)
    object_id   = models.PositiveIntegerField(null=True, blank=True)

    summary     = models.CharField(max_length=300, blank=True)
    detail      = models.JSONField(default=dict, blank=True)
    ip_address  = models.CharField(max_length=64, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['object_type', 'object_id'])]

    def __str__(self):
        return f'{self.actor_name or "System"} {self.action} {self.object_type} #{self.object_id}'


class TreeProfile(models.Model):
    """One row in APIS Tree that the static frontend chart doesn't already
    know about the content of — either an edit to an existing card, or a
    person added on top of the chart entirely.

    APIS Tree's structure — who reports to whom — is defined in frontend
    source (features/tree/APIS_TREE.tsx) since it rarely changes and drives
    the chart's own layout. What changes more often — a person's name,
    title, department, photo, or the roster itself gaining someone new —
    lives here instead.

    Two shapes share this one table, distinguished by `is_new`:

    - An OVERRIDE (`is_new=False`) corrects one of the chart's own cards.
      `person_id` matches that card's stable frontend id (a HOD's own
      `.id`, or a slug for a sub-tree member — see memberId() in
      APIS_TREE.tsx) and `parent_hod_id` is blank. A row only exists once
      someone has actually edited that card; until then the frontend's own
      baseline value shows.
    - An ADDITION (`is_new=True`) is a person who isn't in the static chart
      at all. `person_id` is server-generated (nothing on the frontend
      refers to it yet), and `parent_hod_id` says where it renders: blank
      for a new top-level HOD card, or an existing HOD's `.id` for someone
      added into that HOD's own sub-tree. Deleting an addition removes the
      person outright — there's no baseline underneath it to revert to,
      unlike an override.

    A third flag, `is_hidden`, is independent of the above: the static
    chart has no delete of its own (its cards are hardcoded frontend data,
    not database rows), so removing one of ITS people is recorded as an
    override with `is_hidden=True` rather than an actual deletion — the
    row is what remembers "don't render this card", the same way an
    override row remembers a name change. An added person has no baseline
    to hide, so removing one deletes its row outright instead.
    """
    person_id  = models.CharField(max_length=150, unique=True, db_index=True)
    name       = models.CharField(max_length=200, blank=True)
    role       = models.CharField(max_length=200, blank=True)
    department = models.CharField(max_length=200, blank=True)
    photo      = models.ImageField(upload_to='orgtree/%Y/%m/', null=True, blank=True)
    # Only meaningful when is_new=True — see class docstring.
    is_new        = models.BooleanField(default=False)
    parent_hod_id = models.CharField(max_length=150, blank=True, db_index=True)
    # Only ever set on a static card's own override (is_new=False) — see
    # class docstring for why this and is_new mean different things.
    is_hidden     = models.BooleanField(default=False)
    updated_by_name  = models.CharField(max_length=200, blank=True)
    updated_by_email = models.CharField(max_length=254, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['person_id']

    def __str__(self):
        return self.person_id
