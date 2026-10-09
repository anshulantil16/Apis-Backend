"""The report as a PDF.

An .html attachment is correct and unreadable. Gmail previews a PDF; it
shows an .html file as its own source, so the first thing sixteen people
would see each morning is a wall of CSS, and only a download gets them the
report. That is not an attachment anybody opens twice.

Rendered from the same HTML the screen shows rather than redrawn: a second
renderer is a second set of numbers to keep in agreement, and the whole
point of building the report in one place was that there is only ever one
answer.

WeasyPrint is optional on purpose. If it is not installed the send falls
back to attaching the HTML, which is what it did before -- a missing
rendering library should degrade the attachment, not stop the morning mail.
"""
_ENGINE = None

# A4 with real margins, and page numbers, because this gets printed and
# carried into reviews.
PAGE_CSS = """
@page {
  size: A4;
  margin: 13mm 11mm 15mm;
  @bottom-right {
    content: counter(page) " / " counter(pages);
    font-family: sans-serif; font-size: 8pt; color: #8A94A6;
  }
}
body { background: #FFFFFF; font-size: 10.5pt; }
.wrap { padding-block: 0 0; }
section { break-inside: avoid; }
figure, .tile { break-inside: avoid; }
"""


def _engine():
    """-> weasyprint.HTML, or False once we know it is not there."""
    global _ENGINE
    if _ENGINE is None:
        try:
            from weasyprint import HTML
            _ENGINE = HTML
        except Exception:
            # ImportError when the package is missing, OSError when it is
            # installed but its system libraries are not -- which looks
            # identical from here and has the same answer.
            _ENGINE = False
    return _ENGINE


def available():
    return bool(_engine())


def render(html):
    """-> PDF bytes, or None if nothing can render it."""
    HTML = _engine()
    if not HTML:
        return None
    try:
        from weasyprint import CSS
        return HTML(string=html).write_pdf(stylesheets=[CSS(string=PAGE_CSS)])
    except Exception:
        # A report that will not render is a report that goes out as HTML,
        # not a send that stops. The caller says which it attached.
        return None
