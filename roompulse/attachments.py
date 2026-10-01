"""Serving the files people attach to tickets.

Two things were wrong with handing out `/media/...` URLs for these.

**The browser could not fetch them.** The link was built with
`build_absolute_uri`, so its host is whatever host reached Django. Behind the
QA proxy that is not necessarily the host the browser can reach, and `/media/`
is not under `/api/`, which is the path the proxy actually forwards. Either
one alone produces a link that simply does not load, which is what IT Support
saw when they opened a screenshot on a ticket.

**Anyone could fetch them.** `config/urls.py` serves MEDIA_ROOT to the world
with no authentication, so every screenshot and log file anyone had ever
attached to a ticket was readable by anybody who had, or guessed, the path.
These are not public documents.

So attachments are served from here instead: a path under `/api/`, which the
proxy forwards, carrying a signed token rather than a filename.

The token is signed and timestamped rather than the endpoint simply checking
the session, because it has to work from an `<img src>` and from "open in a
new tab" — neither of which can send the `X-AdminPulse-Session` header. A
signed link is the standard answer to that, and it is a large improvement on
a permanently public path: it cannot be guessed from the filename, it names
one attachment, and it stops working.
"""
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.http import FileResponse, Http404
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import TicketAttachment

# Long enough that a link still works if somebody opens a ticket, goes to a
# meeting and comes back to it; short enough that a link pasted into a chat is
# not a permanent key to the file.
MAX_AGE_SECONDS = 12 * 60 * 60

_signer = TimestampSigner(salt='roompulse.attachment')


def token_for(attachment):
    """The opaque, expiring reference that stands in for a file path."""
    return _signer.sign(str(attachment.id))


def url_for(attachment):
    """A root-relative path, deliberately not an absolute URL.

    build_absolute_uri answers with whatever host reached Django, which behind
    a proxy can be an internal one the browser cannot resolve. The frontend
    already knows where the API lives; it prefixes this itself.
    """
    return f'/api/roompulse/attachments/{token_for(attachment)}/'


class AttachmentView(APIView):
    """GET — the file behind a signed token."""

    def get(self, request, token):
        try:
            raw_id = _signer.unsign(token, max_age=MAX_AGE_SECONDS)
        except SignatureExpired:
            return Response(
                {'error': 'This link has expired. Re-open the ticket for a fresh one.'},
                status=410)
        except BadSignature:
            # Deliberately the same answer as a missing file: a caller probing
            # tokens learns nothing about which ones exist.
            raise Http404

        attachment = TicketAttachment.objects.filter(pk=raw_id).first()
        if not attachment or not attachment.file:
            raise Http404

        try:
            handle = attachment.file.open('rb')
        except (FileNotFoundError, OSError):
            # The row can outlive the file if media was not carried across a
            # server move. Saying so beats a 500.
            return Response(
                {'error': 'That file is no longer on the server.'}, status=404)

        response = FileResponse(handle, filename=attachment.original_name or 'attachment')
        # Uploads are attacker-supplied bytes. Ticket uploads are already
        # restricted by type on the way in (see views/tickets.py), and this
        # stops a browser second-guessing that on the way out.
        response['X-Content-Type-Options'] = 'nosniff'
        return response
