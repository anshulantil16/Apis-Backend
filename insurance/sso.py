"""Minting the partner SSO token that signs an employee in to the insurer's
Employee Portal.

Where this comes from
---------------------
The insurer gave us "SSO Auto Login - Partner Integration Guide v1.0". In
that document **we are the partner** and the insurer runs the portal: we
identify the employee, encrypt their details with a shared secret, and send
their browser to the insurer's `/sso/login/<token>`. The insurer decrypts it
and drops them straight onto their e-card, coverage or claim pages without a
second login.

What the document does NOT contain is an endpoint that returns policy data.
There is no JSON API for the e-card, the member list, the sum insured or the
claim status -- only this redirect. So the intranet cannot render those
details itself; it can only take a person to the page that holds them. If we
want them inside the intranet, the insurer has to give us a read API, which
is a separate ask.

Why the token is minted here and never in the browser
-----------------------------------------------------
The shared secret is the whole of the security model: anyone holding it can
mint a token for ANY employee's address and read that person's medical
records. It therefore never leaves this process -- not into a bundle, not
into a URL the frontend builds, not into a log line. The frontend asks for a
launch URL and is handed a finished one.
"""
import base64
import hashlib
import json
import os
from datetime import timezone as dt_timezone
from urllib.parse import quote

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.utils import timezone

# Section 3 of the guide: the only destinations the insurer will redirect to.
# Anything else is refused by them, so it is refused here rather than minting
# a token that lands on their error page.
DESTINATIONS = {
    'my-coverage':          'My Coverage',
    'members-covered':      'Members Covered',
    'policy-details':       'Policy Details',
    'network-hospitals':    'Network Hospitals',
    'blacklisted-hospitals': 'Blacklisted Hospitals',
    'intimate-claims':      'Intimate a Claim',
    'claim-submission':     'Claim Submission',
    'claim-status':         'Claim Status',
    'download-forms':       'Download Forms',
    'escalation-matrix':    'Escalation Matrix',
    'wellness-corner':      'Wellness Corner',
}

IV_BYTES = 12    # section 4: a fresh random 12 bytes for every request
TAG_BYTES = 16   # section 4: the full 128-bit tag, never truncated

# Section 7 gives the token three hours. We mint one per click and it is spent
# immediately, so this is only here to be compared against -- a clock that has
# drifted past it is worth saying out loud rather than letting the insurer
# answer with a blank error page.
VALIDITY_MINUTES = 180


# How to read their responses, learned by trying nine variants against the
# live endpoint on 23-11-2026:
#
#   bare HTTP 500          -> THE KEY IS WRONG. Decryption threw and their
#                             application did not catch it. Nothing about the
#                             payload can cause this.
#   their SSO error page   -> the key is right. The token decrypted, parsed
#                             and passed their checks; whatever the page says
#                             is a real answer about the DATA.
#
# That distinction is the whole diagnostic. Three wrong-key variants (raw
# secret as key, SHA of the lowercased secret, SHA of the secret's hex
# string) all returned 500; six variants carrying SHA-256 of the secret as
# issued -- differing in field names, timestamp format and destination --
# all reached the error page. So SHA-256 over the secret exactly as issued
# is confirmed, and their parser is far more forgiving about the payload
# than the document suggests.


class NotConfigured(RuntimeError):
    """The secret or the base URL is missing, so nothing can be minted."""


def _b64url(raw):
    """Base64 URL-safe, padding stripped -- section 4's encoding."""
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def configured():
    return bool(settings.INSURANCE_SSO_SECRET and settings.INSURANCE_SSO_BASE_URL)


def _key():
    """32 bytes derived from the shared secret with SHA-256 (section 4).

    The digest of the secret, not of its hex or its base64 -- the guide says
    "derived from the shared secret using SHA-256" and nothing more, so the
    secret's own bytes go in. If the insurer's side rejects every token while
    the format is right, this line is the first thing to check with them.
    """
    return hashlib.sha256(settings.INSURANCE_SSO_SECRET.encode('utf-8')).digest()


def _timestamp():
    """The request time. **ISO 8601 UTC, not Unix seconds.**

    Their section 3 says "Use a Unix timestamp in seconds" and offers ISO as
    merely also accepted. Observed behaviour against the live endpoint is the
    reverse: an integer timestamp returns a bare HTTP 500, and the identical
    payload carrying an ISO string reaches their own SSO error page. Trust
    the endpoint over the document -- shipping the documented form cost a
    broken production deploy on 23-11-2026.

    That the 180-minute check passes at all settles the timezone question:
    we send UTC, their answer came back about the EMAIL, and their section 8
    validates the timestamp before it looks the email up. Nothing needs
    shifting to IST.
    """
    now = timezone.now().astimezone(dt_timezone.utc)
    if str(getattr(settings, 'INSURANCE_SSO_TIMESTAMP', 'iso')).lower() == 'unix':
        return int(now.timestamp())
    return now.strftime('%Y-%m-%dT%H:%M:%SZ')


def payload_for(user, destination=''):
    """The JSON body described in section 3, for one employee.

    `email` is required and carries the identity. Roughly fifty people in the
    directory have no address at all (HRMS never held one), and section 3
    allows the employee code in that case -- so the code is always sent as
    well, which costs nothing and is the only thing that will work for them.
    """
    body = {
        'timestamp': _timestamp(),
    }
    if user.email:
        body['email'] = user.email

    # The employee code goes in only when there is no email -- which is the
    # case section 3 allows it for, and the ~50 directory entries HRMS has no
    # address for.
    #
    # It is held back otherwise ON PURPOSE. Asked to confirm this field's
    # exact JSON key, the insurer answered "we need all the parameters in the
    # JSON encrypted format", which does not say whether it is employee_id,
    # employeeId or something else. So the name is still a guess, and sending
    # a guessed key to everybody risks breaking sign-in for the whole company
    # if their parser is strict. Sent only to the people who cannot be
    # identified any other way, a wrong guess costs those fifty their login
    # and nobody else's -- and that is the group whose first failure tells us
    # the right name.
    #
    # Set INSURANCE_SSO_ALWAYS_SEND_CODE=1 once the insurer confirms the key.
    # Default ON since 23-11-2026: the live test that reached their error page
    # carried employee_id alongside the email, so the extra key is demonstrably
    # not what their parser objects to. Whether they READ it is still untested
    # -- that needs one of the ~50 people who have no email at all.
    always = str(getattr(settings, 'INSURANCE_SSO_ALWAYS_SEND_CODE', '1')).lower() in ('1', 'true', 'yes')
    if user.employee_code and (always or not user.email):
        body[settings.INSURANCE_SSO_EMPLOYEE_ID_FIELD] = user.employee_code

    if destination:
        body['destination'] = destination
    return body


def make_token(payload):
    """`BASE64URL(IV).BASE64URL(CIPHERTEXT).BASE64URL(TAG)` -- section 5.

    `AESGCM.encrypt` returns the ciphertext with the tag appended, which is
    the usual convention and NOT what the insurer wants: they want the three
    parts separately, so the tag is split back off the end.
    """
    if not configured():
        raise NotConfigured('The insurance SSO secret or base URL is not set.')
    iv = os.urandom(IV_BYTES)
    sealed = AESGCM(_key()).encrypt(iv, json.dumps(payload).encode('utf-8'), None)
    ciphertext, tag = sealed[:-TAG_BYTES], sealed[-TAG_BYTES:]
    return '.'.join(_b64url(part) for part in (iv, ciphertext, tag))


def launch_url(user, destination=''):
    """The full HTTPS URL to send this employee's browser to."""
    if destination and destination not in DESTINATIONS:
        raise ValueError(f'{destination!r} is not a destination the insurer accepts.')
    token = make_token(payload_for(user, destination))
    base = settings.INSURANCE_SSO_BASE_URL.rstrip('/')
    return f'{base}/sso/login/{quote(token, safe="")}'


def portal_name():
    """What the insurer's portal is called on screen.

    Settable because the document never names the insurer -- it says "our
    Employee Portal" throughout -- so until somebody tells us the brand, a
    neutral label is the honest one.
    """
    return settings.INSURANCE_PORTAL_NAME or 'Employee Insurance Portal'
