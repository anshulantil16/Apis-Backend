"""The portal's front door, and the console behind it.

Sign-in is email + a one-time code. There is no password anywhere in this
system: the company already trusts its mailboxes, and a password is one more
thing to leak, reset and re-use badly.

Everything below is deliberately explicit about failure. A door that says
"something went wrong" teaches people to file a ticket; a door that says
"that code has expired, ask for a new one" teaches them to press the button.
"""
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (AppKey, DEFAULT_APPS, SUPERADMIN_BOOTSTRAP_EMAIL,
                     HrmsSyncLog, PortalOTP, PortalSession, PortalUser)
from .services import hrms


# ── helpers ──────────────────────────────────────────────────────────────────
def _mask_email(email):
    """a****l@apisindia.com - enough to recognise your own address, not someone else's."""
    try:
        local, domain = email.split('@', 1)
    except ValueError:
        return email
    if len(local) <= 2:
        return f'{local[0]}***@{domain}'
    return f'{local[0]}{"*" * (len(local) - 2)}{local[-1]}@{domain}'


def _client_ip(request):
    fwd = request.META.get('HTTP_X_FORWARDED_FOR', '')
    return (fwd.split(',')[0].strip() if fwd else request.META.get('REMOTE_ADDR', '')) or ''


def serialize_user(u):
    return {
        'id': u.id,
        'employee_code': u.employee_code,
        'email': u.email,
        'name': u.name,
        'designation': u.designation,
        'department': u.department,
        'location': u.location,
        'reporting_manager_code': u.reporting_manager_code,
        'is_active': u.is_active,
        'is_superadmin': u.is_superadmin,
        'is_bootstrap': u.is_bootstrap_superadmin,
        'allowed_apps': u.allowed_apps,
        'from_hrms': u.from_hrms,
        'last_login_at': u.last_login_at.strftime('%d-%m-%Y %H:%M') if u.last_login_at else None,
        'last_synced_at': u.last_synced_at.strftime('%d-%m-%Y %H:%M') if u.last_synced_at else None,
    }


def current_session(request):
    """The live session behind this request, or None.

    Read from the Authorization header rather than a cookie so the same
    endpoints serve the SPA and any future non-browser caller identically.
    """
    raw = request.META.get('HTTP_AUTHORIZATION', '')
    token = raw[7:].strip() if raw.lower().startswith('bearer ') else raw.strip()
    if not token:
        return None
    s = (PortalSession.objects
         .select_related('user')
         .filter(token_hash=PortalSession.hash_token(token))
         .first())
    return s if (s and s.is_live and s.user.is_active) else None


def _bootstrap_superadmin():
    """Make sure the founding account exists and is a superadmin.

    Called on every sign-in attempt for that address so the portal is usable
    on a completely empty database - before Pocket HRMS has ever been synced.
    """
    u, created = PortalUser.objects.get_or_create(
        email=SUPERADMIN_BOOTSTRAP_EMAIL,
        defaults={'employee_code': 'APIS-ADMIN', 'name': 'Anshul Antil',
                  'designation': 'Administrator', 'department': 'IT',
                  'app_access': [c.value for c in AppKey]})
    if not (u.is_superadmin and u.is_active):
        u.is_superadmin = True
        u.is_active = True
        u.save(update_fields=['is_superadmin', 'is_active'])
    return u


def _dev_login():
    """True only on a developer's machine, and only if they opted in.

    Read through getattr so an older .env or a settings module without the
    flag simply means 'off' rather than an AttributeError at sign-in.
    """
    return bool(getattr(settings, 'PORTAL_DEV_LOGIN', False))


class PortalAPIView(APIView):
    """Base for every portal endpoint.

    Opts out of DRF's project-wide JWTAuthentication. These views carry their
    own opaque session tokens in the same Authorization header, and SimpleJWT
    would try to parse one as a JWT, fail, and answer 401 before any code here
    ran — so a perfectly good session looked like a rejected one.
    """
    authentication_classes = []
    permission_classes = []


# ── sign-in ──────────────────────────────────────────────────────────────────
class RequestOTPView(PortalAPIView):
    """Step one: prove you own a company mailbox."""

    def post(self, request):
        email = (request.data.get('email') or '').strip().lower()
        if not email or '@' not in email:
            return Response({'error': 'Enter your work email address.'}, status=400)

        if email == SUPERADMIN_BOOTSTRAP_EMAIL.lower():
            user = _bootstrap_superadmin()
        else:
            user = PortalUser.objects.filter(email__iexact=email, is_active=True).first()

        # On a developer's machine there is no HRMS sync, so the directory is
        # empty and nobody but the bootstrap account can get in. Register a
        # company address on first use so the whole team can run the app
        # locally. Off unless PORTAL_DEV_LOGIN=1 is in their own .env.
        #
        # Note the second condition. The lookup above filters on is_active, so
        # a DISABLED person also comes back as None — creating then would both
        # break on the unique email and, but for that constraint, hand a
        # switched-off account a way back in. Someone who has been disabled
        # must stay disabled, on a developer's machine as much as anywhere.
        if (user is None and _dev_login() and email.endswith('@apisindia.com')
                and not PortalUser.objects.filter(email__iexact=email).exists()):
            user = PortalUser.objects.create(
                email=email, name=email.split('@')[0].replace('.', ' ').title(),
                employee_code=f'DEV-{email.split("@")[0][:12]}',
                designation='Developer', department='IT',
                app_access=[c.value for c in AppKey])

        # A wrong address and an unknown address answer identically. This
        # endpoint is reachable from the internet, and answering "no such
        # person" turns it into a directory of who works here.
        generic = Response({'message': f'If {email} is registered, a sign-in code is on its way.',
                            'masked_email': _mask_email(email)})
        if not user:
            return generic

        # Rate limit per account, so a stolen address cannot be used to flood
        # someone's inbox or to grind through codes by asking for new ones.
        recent = PortalOTP.objects.filter(
            user=user, created_at__gte=timezone.now() - PortalOTP.RESEND_WINDOW).count()
        if recent >= PortalOTP.MAX_PER_WINDOW:
            return Response({'error': 'Too many codes requested. Please wait a few minutes '
                                      'and try again.'}, status=429)

        code = PortalOTP.issue(user)

        # Locally there are no SMTP credentials, so emailing the code would
        # fail and lock the developer out of their own build. Hand it back in
        # the response instead — the login screen fills it in. This is the one
        # place a code is ever exposed, and it cannot happen on a server:
        # PORTAL_DEV_LOGIN is absent from every deployed .env.
        if _dev_login():
            print(f'[portal] dev sign-in code for {email}: {code}')
            return Response({**generic.data, 'dev_otp': code})

        try:
            send_mail(
                subject='Your APIS Intranet sign-in code',
                message=(f'Hi {user.name.split()[0] if user.name else "there"},\n\n'
                         f'Your sign-in code for the APIS Intranet is:\n\n    {code}\n\n'
                         f'It is valid for 5 minutes and can be used once.\n'
                         f'If you did not ask to sign in, you can ignore this email — '
                         f'nobody can get in without this code.\n\n'
                         f'— APIS Intranet'),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email], fail_silently=False)
        except Exception as e:
            return Response({'error': f'Could not send the code: {e}'}, status=500)
        return generic


class VerifyOTPView(PortalAPIView):
    """Step two: the code, in exchange for a session."""

    def post(self, request):
        email = (request.data.get('email') or '').strip().lower()
        code = (request.data.get('otp') or '').strip()
        if not email or not code:
            return Response({'error': 'Enter the 6-digit code we emailed you.'}, status=400)

        user = PortalUser.objects.filter(email__iexact=email, is_active=True).first()
        if not user:
            return Response({'error': 'That code is not valid. Please request a new one.'}, status=400)

        otp = PortalOTP.objects.filter(user=user, is_used=False).order_by('-created_at').first()
        if not otp:
            return Response({'error': 'No sign-in code outstanding. Please request one.'}, status=400)
        if otp.expires_at <= timezone.now():
            return Response({'error': 'That code has expired. Please request a new one.'}, status=400)
        if otp.attempts >= PortalOTP.MAX_ATTEMPTS:
            return Response({'error': 'Too many incorrect attempts. Please request a new code.'},
                            status=429)

        if not otp.matches(code):
            left = max(0, PortalOTP.MAX_ATTEMPTS - otp.attempts)
            if left == 0:
                otp.is_used = True
                otp.save(update_fields=['is_used'])
                return Response({'error': 'Too many incorrect attempts. Please request a new code.'},
                                status=429)
            return Response({'error': f'That code is not right. {left} attempt'
                                      f'{"" if left == 1 else "s"} left.'}, status=400)

        otp.is_used = True
        otp.save(update_fields=['is_used'])
        token = PortalSession.start(user, request.META.get('HTTP_USER_AGENT', ''), _client_ip(request))
        user.last_login_at = timezone.now()
        user.save(update_fields=['last_login_at'])
        return Response({'message': 'Signed in.', 'token': token, 'user': serialize_user(user)})


class MeView(PortalAPIView):
    """Who am I, and what may I open? The SPA's gate on every load."""

    def get(self, request):
        s = current_session(request)
        if not s:
            return Response({'error': 'Not signed in.'}, status=401)
        s.touch()
        return Response({'user': serialize_user(s.user)})


class LogoutView(PortalAPIView):
    def post(self, request):
        s = current_session(request)
        if s:
            s.revoked_at = timezone.now()
            s.save(update_fields=['revoked_at'])
        # Signing out something already signed out is a success, not an error.
        return Response({'message': 'Signed out.'})


# ── console (superadmin only) ────────────────────────────────────────────────
class _AdminView(PortalAPIView):
    """Shared gate: everything below is superadmin-only."""

    def _guard(self, request):
        s = current_session(request)
        if not s:
            return None, Response({'error': 'Not signed in.'}, status=401)
        if not s.user.is_superadmin:
            return None, Response({'error': 'This console is for administrators.'}, status=403)
        return s, None


class AdminUsersView(_AdminView):
    """The directory, and the access controls over it."""

    def get(self, request):
        s, err = self._guard(request)
        if err:
            return err
        q = (request.query_params.get('q') or '').strip()
        rows = PortalUser.objects.all()
        if q:
            from django.db.models import Q
            rows = rows.filter(Q(name__icontains=q) | Q(email__icontains=q)
                               | Q(employee_code__icontains=q) | Q(department__icontains=q))
        return Response({
            'users': [serialize_user(u) for u in rows[:500]],
            'total': PortalUser.objects.count(),
            'active': PortalUser.objects.filter(is_active=True).count(),
            'superadmins': PortalUser.objects.filter(is_superadmin=True).count(),
            'apps': [{'key': c.value, 'label': c.label} for c in AppKey],
            'hrms_configured': hrms.is_configured(),
        })

    def post(self, request):
        """Create a person the HRMS does not know about yet (contractor, new joiner)."""
        s, err = self._guard(request)
        if err:
            return err
        email = (request.data.get('email') or '').strip().lower()
        name = (request.data.get('name') or '').strip()
        code = (request.data.get('employee_code') or '').strip()
        if not email or '@' not in email or not name or not code:
            return Response({'error': 'Employee code, name and email are all needed.'}, status=400)
        if PortalUser.objects.filter(email__iexact=email).exists():
            return Response({'error': 'Someone already has that email.'}, status=400)
        if PortalUser.objects.filter(employee_code=code).exists():
            return Response({'error': 'That employee code is already in use.'}, status=400)
        u = PortalUser.objects.create(
            employee_code=code, email=email, name=name,
            designation=(request.data.get('designation') or '').strip(),
            department=(request.data.get('department') or '').strip(),
            location=(request.data.get('location') or '').strip(),
            # An empty list means "grant nothing", which is a deliberate choice;
            # `or DEFAULT_APPS` read it as "not specified" and quietly handed the
            # person three tools the administrator had just unticked.
            app_access=(list(request.data['app_access'])
                        if isinstance(request.data.get('app_access'), list)
                        else list(DEFAULT_APPS)))
        return Response({'message': f'{u.name} can now sign in.', 'user': serialize_user(u)})


class AdminUserDetailView(_AdminView):
    """Inspect and change one person."""

    def get(self, request, user_id):
        """Everything the portal holds on this person, including what Pocket
        HRMS sent verbatim - so a wrong department or a missing email can be
        traced to upstream rather than guessed at."""
        s, err = self._guard(request)
        if err:
            return err
        u = PortalUser.objects.filter(id=user_id).first()
        if not u:
            return Response({'error': 'No such user.'}, status=404)
        return Response({
            'user': serialize_user(u),
            'hrms_raw': u.hrms_raw or {},
            'hrms_fields_requested': hrms.EMPLOYEE_FIELDS,
            'sessions': [{
                'id': x.id, 'ip_address': x.ip_address,
                'user_agent': x.user_agent[:120],
                'last_seen_at': x.last_seen_at.strftime('%d-%m-%Y %H:%M'),
            } for x in u.sessions.filter(revoked_at__isnull=True,
                                         expires_at__gt=timezone.now())[:20]],
        })

    def patch(self, request, user_id):
        s, err = self._guard(request)
        if err:
            return err
        u = PortalUser.objects.filter(id=user_id).first()
        if not u:
            return Response({'error': 'No such user.'}, status=404)

        d = request.data
        # The founding account is the way back in when something goes wrong.
        # Refusing here rather than trusting the UI to hide the switch.
        if u.is_bootstrap_superadmin and (d.get('is_active') is False
                                          or d.get('is_superadmin') is False):
            return Response({'error': 'The founding administrator account cannot be '
                                      'disabled or demoted — it is the way back in.'}, status=400)
        # Nor can the last superadmin standing remove themselves.
        if d.get('is_superadmin') is False and u.is_superadmin:
            if PortalUser.objects.filter(is_superadmin=True, is_active=True).count() <= 1:
                return Response({'error': 'That is the last administrator. Promote someone '
                                          'else first.'}, status=400)

        if 'is_active' in d:
            u.is_active = bool(d['is_active'])
        if 'is_superadmin' in d:
            u.is_superadmin = bool(d['is_superadmin'])
        if 'app_access' in d:
            wanted = d['app_access'] or []
            unknown = [a for a in wanted if a not in AppKey.values]
            if unknown:
                return Response({'error': f'Unknown app(s): {", ".join(unknown)}'}, status=400)
            u.app_access = list(wanted)
        # Email is the sign-in identity, so it is editable but guarded: a typo in
        # an address locks that person out with no clue why, and a duplicate
        # would make two people answer to one sign-in.
        if 'email' in d:
            email = (d['email'] or '').strip().lower()
            if not email or '@' not in email:
                return Response({'error': 'That is not an email address.'}, status=400)
            if u.is_bootstrap_superadmin and email != SUPERADMIN_BOOTSTRAP_EMAIL.lower():
                return Response({'error': 'The founding account is identified by its '
                                          'address, so that one cannot be changed.'}, status=400)
            if PortalUser.objects.filter(email__iexact=email).exclude(id=u.id).exists():
                return Response({'error': 'Someone else already has that address.'}, status=400)
            u.email = email

        if 'employee_code' in d:
            code = (d['employee_code'] or '').strip()
            if not code:
                return Response({'error': 'An employee code is required.'}, status=400)
            if PortalUser.objects.filter(employee_code__iexact=code).exclude(id=u.id).exists():
                return Response({'error': 'That employee code is already in use.'}, status=400)
            u.employee_code = code

        for f in ('name', 'designation', 'department', 'location',
                  'reporting_manager_code'):
            if f in d:
                setattr(u, f, (d[f] or '').strip())
        u.save()

        # Access taken away should mean access taken away now, not in 12 hours.
        if not u.is_active:
            u.sessions.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
        return Response({'message': f'{u.name} updated.', 'user': serialize_user(u)})


    def delete(self, request, user_id):
        """Remove someone from the portal for good.

        Distinct from disabling, which is the right answer for a leaver: it
        keeps the record and can be undone. This is for a row that should never
        have existed - a duplicate, a test account, a typo'd import - so it
        takes the person's own name as confirmation rather than a yes/no.

        Anyone synced from Pocket HRMS comes straight back on the next sync, so
        the response says so rather than letting an admin think it stuck.
        """
        s, err = self._guard(request)
        if err:
            return err
        u = PortalUser.objects.filter(id=user_id).first()
        if not u:
            return Response({'error': 'No such user.'}, status=404)

        if u.is_bootstrap_superadmin:
            return Response({'error': 'The founding administrator cannot be removed — '
                                      'it is the way back in.'}, status=400)
        if u.is_superadmin and PortalUser.objects.filter(
                is_superadmin=True, is_active=True).count() <= 1:
            return Response({'error': 'That is the last administrator. Promote someone '
                                      'else first.'}, status=400)
        if (request.data.get('confirm_name') or '').strip().lower() != u.name.strip().lower():
            return Response({'error': f'Type "{u.name}" to confirm the removal.'}, status=400)

        name, from_hrms = u.name, u.from_hrms
        u.delete()
        return Response({
            'message': f'{name} removed.',
            'warning': ('This person came from Pocket HRMS and will reappear on the next '
                        'sync. Disable them instead if you want them to stay out.')
            if from_hrms else '',
        })


class AdminBulkAccessView(_AdminView):
    """Grant or revoke one tool across many people at once.

    Opening thirty drawers to give thirty people the same tool is how an
    administrator ends up not bothering, and everyone gets left with whatever
    the default was.
    """

    def post(self, request):
        s, err = self._guard(request)
        if err:
            return err

        ids = request.data.get('user_ids') or []
        action = str(request.data.get('action') or '')
        app = str(request.data.get('app') or '')

        if not ids:
            return Response({'error': 'Choose some people first.'}, status=400)
        if action not in ('grant', 'revoke', 'enable', 'disable'):
            return Response({'error': 'Unknown action.'}, status=400)
        if action in ('grant', 'revoke') and app not in AppKey.values:
            return Response({'error': f'Unknown tool "{app}".'}, status=400)

        people = PortalUser.objects.filter(id__in=ids)
        changed, skipped = 0, []

        for u in people:
            if action == 'disable':
                if u.is_bootstrap_superadmin:
                    skipped.append(f'{u.name} (founding account)')
                    continue
                u.is_active = False
                u.sessions.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
            elif action == 'enable':
                u.is_active = True
            else:
                access = list(u.app_access or [])
                if action == 'grant' and app not in access:
                    access.append(app)
                elif action == 'revoke' and app in access:
                    access.remove(app)
                else:
                    continue        # already in the state asked for
                u.app_access = access
            u.save()
            changed += 1

        return Response({'changed': changed, 'skipped': skipped,
                         'message': f'{changed} '
                                    f'{"person" if changed == 1 else "people"} updated.'})


class AdminSyncView(_AdminView):
    """Pull the employee master from Pocket HRMS."""

    def get(self, request):
        s, err = self._guard(request)
        if err:
            return err
        return Response({
            'configured': hrms.is_configured(),
            'base_url': getattr(settings, 'POCKET_HRMS_BASE_URL', ''),
            'logs': [{
                'id': l.id, 'ok': l.ok,
                'started_at': l.started_at.strftime('%d-%m-%Y %H:%M'),
                'triggered_by': l.triggered_by, 'fetched': l.fetched,
                'created': l.created, 'updated': l.updated,
                'deactivated': l.deactivated, 'skipped_no_email': l.skipped_no_email,
                'message': l.message,
            } for l in HrmsSyncLog.objects.all()[:25]],
        })

    def post(self, request):
        s, err = self._guard(request)
        if err:
            return err
        if not hrms.is_configured():
            return Response({'error': 'No Pocket HRMS token configured yet. Set '
                                      'POCKET_HRMS_TOKEN on the server first.'}, status=400)
        days = request.data.get('modified_since_days')
        since = None
        if days:
            try:
                since = (timezone.now() - timedelta(days=int(days))).date()
            except (TypeError, ValueError):
                return Response({'error': 'modified_since_days must be a number.'}, status=400)
        try:
            log = hrms.sync_employees(triggered_by=s.user.email, modified_since=since)
        except hrms.HrmsError as e:
            return Response({'error': str(e)}, status=502)
        return Response({'message': log.message, 'log': {
            'ok': log.ok, 'fetched': log.fetched, 'created': log.created,
            'updated': log.updated, 'deactivated': log.deactivated,
            'skipped_no_email': log.skipped_no_email,
        }})


class AdminHrmsPreviewView(_AdminView):
    """A live peek at the raw employee master, straight from Pocket HRMS.

    Deliberately separate from a sync: this writes nothing. It is how an
    administrator answers "what is upstream actually sending?" - including
    which columns exist at all, which is the only reliable way to tell whether
    a field the portal wants is available on this tenant.
    """

    def get(self, request):
        s, err = self._guard(request)
        if err:
            return err
        if not hrms.is_configured():
            return Response({'error': 'No Pocket HRMS token configured yet. Set '
                                      'POCKET_HRMS_TOKEN on the server first.',
                             'configured': False}, status=400)
        try:
            limit = min(int(request.query_params.get('limit') or 5), 25)
        except (TypeError, ValueError):
            limit = 5

        # ?discover=1 leaves the EmployeeFields header off entirely, which is
        # Pocket HRMS support's own recommended way to find this tenant's real
        # configured column names rather than guess at EmailId vs Email.
        discover = request.query_params.get('discover') in ('1', 'true', 'True')
        try:
            if discover:
                columns, rows = hrms.discover_fields(sample_size=limit)
            else:
                rows = hrms.fetch_page(take=limit, offset=0, emp_status='ALL')
                # The union of keys across the sample, so a column absent from
                # the first record is still reported rather than silently missed.
                columns = sorted({k for r in rows if isinstance(r, dict) for k in r})
        except hrms.HrmsError as e:
            return Response({'error': str(e), 'configured': True}, status=502)

        return Response({
            'configured': True,
            'discovered': discover,
            'requested_fields': hrms.EMPLOYEE_FIELDS if not discover else None,
            'returned_columns': columns,
            'sample': rows,
            'count': len(rows),
        })


class AdminSessionsView(_AdminView):
    """Who is signed in, and the ability to end it."""

    def get(self, request):
        s, err = self._guard(request)
        if err:
            return err
        live = (PortalSession.objects.select_related('user')
                .filter(revoked_at__isnull=True, expires_at__gt=timezone.now()))
        return Response({'sessions': [{
            'id': x.id, 'name': x.user.name, 'email': x.user.email,
            'ip_address': x.ip_address, 'user_agent': x.user_agent[:120],
            'last_seen_at': x.last_seen_at.strftime('%d-%m-%Y %H:%M'),
            'is_you': x.id == s.id,
        } for x in live[:200]]})

    def delete(self, request, session_id=None):
        s, err = self._guard(request)
        if err:
            return err
        x = PortalSession.objects.filter(id=session_id).first()
        if not x:
            return Response({'error': 'No such session.'}, status=404)
        x.revoked_at = timezone.now()
        x.save(update_fields=['revoked_at'])
        return Response({'message': 'Session ended.'})
