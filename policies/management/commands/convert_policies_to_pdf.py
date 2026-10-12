"""Convert documents filed before uploads were turned into PDFs.

    ./venv/Scripts/python.exe manage.py convert_policies_to_pdf [--dry-run]

Each Word / Excel / PowerPoint file in the register is replaced by its PDF:
the row keeps its id, title and sign-off, the file and page count change,
and the old file is deleted once the new one is saved. Safe to run twice —
a document that is already a PDF is skipped.
"""
import os

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from policies.models import PolicyDocument
from policies.pdf_convert import ConversionError, convert_to_pdf
from policies.views import _count_pdf_pages


class Command(BaseCommand):
    help = 'Convert non-PDF policy documents to PDF.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='List what would be converted without changing anything.')

    def handle(self, *args, dry_run=False, **options):
        done = failed = 0
        for d in PolicyDocument.objects.all():
            ext = os.path.splitext(d.file.name or '')[1].lower()
            if not d.file or ext == '.pdf':
                continue
            label = f'#{d.id} {d.title} ({d.original_name or d.file.name})'
            if dry_run:
                self.stdout.write(f'would convert {label}')
                continue
            try:
                with d.file.open('rb') as fh:
                    data = convert_to_pdf(fh, ext)
            except (ConversionError, OSError) as e:
                failed += 1
                self.stderr.write(f'FAILED {label}: {e}')
                continue

            old = d.file.name
            stem = os.path.splitext(os.path.basename(d.original_name or 'document'))[0] or 'document'
            pdf = ContentFile(data, name=f'{stem}.pdf')
            d.pages = _count_pdf_pages(pdf)
            d.size_bytes = len(data)
            d.file.save(pdf.name, pdf, save=False)
            d.save(update_fields=['file', 'pages', 'size_bytes', 'updated_at'])
            d.file.storage.delete(old)
            done += 1
            self.stdout.write(f'converted {label}')

        if not dry_run:
            self.stdout.write(f'{done} converted, {failed} failed.')
