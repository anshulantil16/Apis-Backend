"""Turn an uploaded Word / Excel / PowerPoint file into a PDF.

Everything in the Policies register is stored and served as a PDF, so that
opening a document shows it in the browser instead of downloading a .docx
that half the readers' phones can't open — and so that what people read is
fixed, not a spreadsheet anyone can edit after saving a copy.

The conversion is LibreOffice in headless mode. It has to be installed on
the machine running Django:

    Linux (QA / Live):  sudo apt install libreoffice-core libreoffice-writer \
                            libreoffice-calc libreoffice-impress
    Windows (local):    install LibreOffice from libreoffice.org

If it is somewhere unusual, point settings.LIBREOFFICE_PATH (or the
LIBREOFFICE_PATH env var) at the soffice binary.
"""
import os
import shutil
import subprocess
import tempfile

from django.conf import settings

TIMEOUT_SECONDS = 120

_WINDOWS_CANDIDATES = (
    r'C:\Program Files\LibreOffice\program\soffice.exe',
    r'C:\Program Files (x86)\LibreOffice\program\soffice.exe',
)


class ConversionError(Exception):
    """The file could not be turned into a PDF. The message is for the
    person who uploaded it."""


def soffice_path():
    """The LibreOffice binary, or None if this machine has none."""
    configured = getattr(settings, 'LIBREOFFICE_PATH', '') or os.environ.get('LIBREOFFICE_PATH', '')
    if configured and os.path.exists(configured):
        return configured
    for name in ('soffice', 'libreoffice'):
        found = shutil.which(name)
        if found:
            return found
    for candidate in _WINDOWS_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


def convert_to_pdf(uploaded, ext):
    """PDF bytes for an uploaded office file. `ext` is its lowercase
    extension, dot included — already checked against the file's magic
    bytes by the caller.

    Runs in a throwaway directory with a throwaway LibreOffice profile: a
    shared profile is locked by whichever conversion is running, and a
    second upload at the same moment would otherwise fail.
    """
    binary = soffice_path()
    if not binary:
        raise ConversionError(
            'Word, Excel and PowerPoint files cannot be converted to PDF on '
            'this server yet. Please save the file as a PDF and upload that.')

    with tempfile.TemporaryDirectory(prefix='policy-pdf-') as work:
        src = os.path.join(work, f'document{ext}')
        with open(src, 'wb') as out:
            uploaded.seek(0)
            for chunk in uploaded.chunks():
                out.write(chunk)
        uploaded.seek(0)

        profile = 'file:///' + os.path.join(work, 'profile').replace('\\', '/').lstrip('/')
        cmd = [binary, f'-env:UserInstallation={profile}', '--headless', '--norestore',
               '--nolockcheck', '--convert-to', 'pdf', '--outdir', work, src]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=TIMEOUT_SECONDS, check=False)
        except subprocess.TimeoutExpired:
            raise ConversionError('Converting that file to PDF took too long. '
                                  'Please save it as a PDF and upload that.')

        pdf = os.path.join(work, 'document.pdf')
        if not os.path.exists(pdf) or os.path.getsize(pdf) == 0:
            raise ConversionError('That file could not be converted to PDF — it may be '
                                  'damaged or password-protected. Please save it as a '
                                  'PDF and upload that.')
        with open(pdf, 'rb') as fh:
            return fh.read()
