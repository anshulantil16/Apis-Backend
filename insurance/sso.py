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


def payload_for(user, destination=''):
    """The JSON body described in section 3, for one employee.

    `email` is required and carries the identity. Roughly fifty people in the
    directory have no address at all (HRMS never held one), and section 3
    allows the employee code in that case -- so the code is always sent as
    well, which costs nothing and is the only thing that will work for them.
    """
    body = {
        'timestamp': int(timezone.now().timestamp()),
    }
    if user.email:
        body['email'] = user.email
    if user.employee_code:
        # The guide's field table writes this one as prose ("Employee ID")
        # while writing the others as identifiers, so its real spelling is
        # not actually pinned down by the document. It is settable rather
        # than hard-coded so that confirming it with the insurer is an .env
        # change, not a release.
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
