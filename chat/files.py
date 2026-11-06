"""What may be attached to a message, and what may not.

An intranet messenger is a file transfer tool whether or not anybody calls
it one, so the list below is an allow-list rather than a block-list: a new
dangerous extension appears every year, and a block-list is always a year
behind.
"""
import os

MAX_BYTES = 15 * 1024 * 1024          # 15 MB
MAX_PER_MESSAGE = 5

# Extension -> the type it is served as. The served type is ours, not the
# browser's upload header, which the sender controls.
IMAGES = {
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
    '.gif': 'image/gif', '.webp': 'image/webp', '.heic': 'image/heic',
}
FILES = {
    '.pdf': 'application/pdf',
    '.doc': 'application/msword',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xls': 'application/vnd.ms-excel',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.ppt': 'application/vnd.ms-powerpoint',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.csv': 'text/csv', '.txt': 'text/plain',
    '.zip': 'application/zip', '.rar': 'application/vnd.rar', '.7z': 'application/x-7z-compressed',
    '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.mp4': 'video/mp4',
}

# Deliberately absent, and worth saying why rather than leaving a gap:
#   .svg  -- an image that is really a document, and can carry script. A
#            browser asked to render one from our own origin runs it.
#   .html .htm .xhtml -- same reason, without the disguise.
#   .exe .msi .bat .cmd .sh .ps1 .js .jar .apk -- nothing here needs to pass
#            an executable between colleagues, and WhatsApp refusing these
#            is the reason people do not expect to.

ALLOWED = {**IMAGES, **FILES}
ACCEPT = ','.join(sorted(ALLOWED))


class RejectedFile(Exception):
    pass


def check(upload):
    """-> (kind, content_type, clean name). Raises RejectedFile with a reason.

    The content type is decided from the extension, never taken from the
    upload. A browser will happily send 'image/png' for an HTML file, and
    serving it back under that claim is how a stored file becomes a script
    running on our own origin.
    """
    raw = (getattr(upload, 'name', '') or '').strip()
    # Only the last component: a name can arrive as a path on some clients.
    name = os.path.basename(raw.replace('\\', '/'))[:300]
    if not name:
        raise RejectedFile('That file has no name.')
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED:
        raise RejectedFile(
            f'{ext or "That file type"} cannot be sent here. '
            'Photos, PDFs, Office files, text, audio and zips are fine.')
    size = getattr(upload, 'size', 0) or 0
    if size <= 0:
        raise RejectedFile(f'{name} is empty.')
    if size > MAX_BYTES:
        raise RejectedFile(f'{name} is {size // (1024 * 1024)} MB. '
                           f'The limit is {MAX_BYTES // (1024 * 1024)} MB.')
    kind = 'image' if ext in IMAGES else 'file'
    return kind, ALLOWED[ext], name
