"""The morning mail: subject, body, and the GTR summary table inside it.

Modelled on the mail the business already sends, so what lands in an inbox
looks like what landed yesterday rather than like a new system announcing
itself. Three things are carried over verbatim, because they are the
business's own statements and not ours to reword:

    FLASH PRIMARY SALES REPORT (B2C)
    * In OT channel Primary = Secondary.
    Note *: This report has been included CN (Sales Return-Damage Expiry &
    Good SR)

What changes is the table. The circulated mail carries every GTR line to
everybody; here it is cut to the reader -- a head sees their own row, a
manager sees their territories and a group line. That is the whole reason
the table is built here rather than pasted in.

It is written as a table of inline-styled cells on purpose. Outlook ignores
stylesheets, <style> blocks, flex and grid; what survives it is a table whose
every cell carries its own style attribute.
"""
from html import escape

from .report import lakh, pct, signed, rag

HEADER_BG = '#1F6FB2'
TONE = {'good': ('#D9EAD3', '#274E13'), 'warn': ('#FCE5CD', '#7F6000'),
        'bad': ('#F4CCCC', '#990000'), 'none': ('#F3F3F3', '#666666')}

_TD = ('padding:5px 8px;border:1px solid #B7C4D4;font-size:12px;'
       'font-family:Calibri,Arial,sans-serif;white-space:nowrap')
_TH = ('padding:6px 8px;border:1px solid #B7C4D4;font-size:11px;font-weight:bold;'
       'font-family:Calibri,Arial,sans-serif;color:#FFFFFF;'
       'background:' + HEADER_BG + ';text-align:center')


def _n(v, dp=2, extra=''):
    """A money cell, right-aligned, in lakhs -- the unit the sheet is read in."""
    return ('<td style="' + _TD + extra + ';text-align:right">'
            + lakh(v, dp) + '</td>')


def _p(v):
    """An achievement cell, shaded the way the sheet shades it."""
    bg, fg = TONE[rag(v)]
    return ('<td style="' + _TD + ';text-align:center;background:' + bg
            + ';color:' + fg + ';font-weight:bold">' + pct(v) + '</td>')


def _g(v):
    """Growth over last month. Green up, red down, as on the sheet."""
    if v is None:
        tone = 'none'
    else:
        tone = 'good' if v > 0 else ('bad' if v < 0 else 'none')
    bg, fg = TONE[tone]
    return ('<td style="' + _TD + ';text-align:center;background:' + bg
            + ';color:' + fg + ';font-weight:bold">' + signed(v) + '</td>')


COLUMNS = ('CHANEL TYPE', 'REGION', 'GTR HEAD', 'NO OF SFO', 'LMTD',
           '{m} AOP', 'MTD {m} PRI SALES', 'MTD Sec sales', 'AOP ACH %',
           'Growth Over LM', 'Backlog TGT FTM', 'YTD AOP', 'YTD ACH',
           'YTD ACH %', 'BACKLOG TGT YTD', 'FY AOP', 'FY ACH', 'FY ACH %',
           'BACKLOG FY')


def summary_table(rows, month_label, totals=None, total_label='Total'):
    """The GTR Summary table, cut to whoever is reading it.

    `rows` are row_json dicts and `totals` is the same shape for a group
    line. Nothing is read off the sheet's own total row: a reader covering
    part of a channel has no subtotal there, and taking the one that is
    there would quietly show them the whole channel as their own.
    """
    mon = month_label or 'MTD'
    up = escape(mon.upper())
    head = (
        '<tr>'
        '<th style="' + _TH + '" colspan="3"></th>'
        '<th style="' + _TH + ';background:#2F7FC4" colspan="8">MTD FOR ' + up + '</th>'
        '<th style="' + _TH + ';background:#3E8E41" colspan="4">YTD TILL ' + up + '</th>'
        '<th style="' + _TH + ';background:#B45F06" colspan="4">FULL YEAR</th>'
        '</tr><tr>'
        + ''.join('<th style="' + _TH + '">' + escape(c.format(m=mon)) + '</th>'
                  for c in COLUMNS)
        + '</tr>')

    def line(r, bold=False, bg=''):
        cell = _TD + (';font-weight:bold' if bold else '') + (
            ';background:' + bg if bg else '')
        o = '<td style="' + cell + '">'
        # The money cells take the row's own styling too, or a bolded group
        # line is bold for its first four cells and ordinary for the rest.
        x = (';font-weight:bold' if bold else '') + (';background:' + bg if bg else '')
        return (
            '<tr>'
            + o + escape(str(r.get('channel') or '')) + '</td>'
            + o + escape(str(r.get('region') or '')) + '</td>'
            + o + escape(str(r.get('head_name') or '')) + '</td>'
            + '<td style="' + cell + ';text-align:center">'
            + str(r.get('sfo_count') or 0) + '</td>'
            + _n(r.get('lmtd'), 2, x) + _n(r.get('month_target'), 2, x)
            + _n(r.get('mtd_primary'), 2, x) + _n(r.get('mtd_secondary'), 2, x)
            + _p(r.get('month_pct')) + _g(r.get('growth_pct'))
            + _n(r.get('month_backlog'), 2, x)
            + _n(r.get('ytd_target'), 2, x) + _n(r.get('ytd_actual'), 2, x)
            + _p(r.get('ytd_pct')) + _n(r.get('ytd_backlog'), 2, x)
            + _n(r.get('fy_target'), 2, x) + _n(r.get('fy_actual'), 2, x)
            # FY ACH % is progress through the annual plan, not a score: it
            # reads low for everybody until March. Shaded like the two above
            # it, it would paint every line on this table red in September.
            + '<td style="' + cell + ';text-align:center">'
            + pct(r.get('fy_pct')) + '</td>'
            + _n(r.get('fy_backlog'), 2, x)
            + '</tr>')

    body = ''.join(line(r) for r in rows)
    if totals:
        t = dict(totals)
        t.setdefault('channel', total_label)
        t.setdefault('region', '')
        t.setdefault('head_name', str(len(rows)) + ' territories')
        body += line(t, bold=True, bg='#DCE6F1')

    return ('<table cellspacing="0" cellpadding="0" border="0" '
            'style="border-collapse:collapse;border:1px solid #B7C4D4">'
            + head + body + '</table>')


# Carried over from the mail the business already circulates, down to the
# yellow highlight and the red note. They are the business's own statements,
# written the way its readers have learnt to look for them -- the highlight
# is how somebody scanning on a phone finds the OT caveat, and reformatting
# it into our own house style would only make the mail harder to read for
# everybody who already reads it.
BODY = 'font-family:Calibri,Arial,sans-serif;font-size:13.5px;color:#000000'
HILITE = 'background:#FFFF00;font-weight:bold'
REDNOTE = ('font-family:Calibri,Arial,sans-serif;font-size:13.5px;'
           'color:#FF0000;margin:14px 0 0')

OT_NOTE = ('<b>*In this sheet, I have put the secondary sales and primary '
           'sales against it, you can see it in the attached.</b> '
           '<span style="' + HILITE + '">*In OT channel Primary = '
           'Secondary.</span>')
CN_NOTE = ('<p style="' + REDNOTE + '">Note *: This report has been included '
           'CN (Sales Return-Damage Expiry &amp; Good SR)</p>')
FOOT = ('<p style="font-family:Calibri,Arial,sans-serif;font-size:11.5px;'
        'color:#777777;margin:18px 0 0">Figures in &#8377; lakhs. '
        'Achievement is measured on primary sales.</p>')


def _shell(greeting, intro, table, tail=''):
    return ('<div style="' + BODY + '">'
            '<p>' + greeting + '</p>'
            '<p>' + intro + '</p>'
            '<p style="font-size:13.5px;font-weight:bold;margin:18px 0 6px">'
            'GTR Summary:-</p>'
            + table + tail + CN_NOTE + FOOT + '</div>')


SUBJECT = 'FLASH PRIMARY SALES REPORT (B2C) - {scope} | (as of {asof})'


# The business writes SEPT'26, not Sep 2026, and the banner row on its own
# sheet reads "MTD FOR SEPT'26". The mail should look like the one that
# landed yesterday.
SHORT = {1: 'JAN', 2: 'FEB', 3: 'MAR', 4: 'APR', 5: 'MAY', 6: 'JUN',
         7: 'JUL', 8: 'AUG', 9: 'SEPT', 10: 'OCT', 11: 'NOV', 12: 'DEC'}


def month_label(snap):
    """-> SEPT'26, from the month the sheet's own column headers carry."""
    m = snap.get('as_of_month')
    if not m:
        return (snap.get('as_of_month_label') or '').upper()
    y, mm = m.split('-')
    return SHORT.get(int(mm), mm) + '&#8217;' + y[2:]


def as_of(snap):
    """The sheet's own date style: 30.09.26."""
    d = snap.get('as_of_date')
    if not d:
        return snap.get('as_of_month_label') or ''
    y, m, dd = d.split('-')
    return dd + '.' + m + '.' + y[2:]


def for_head(d):
    """-> (subject, html) for one head. Their row, and nobody else's."""
    h, y, snap = d['head'], d['year'], d['snapshot']
    first = (h['head_name'] or '').split()[0].title() if h['head_name'] else 'Team'
    mon = snap.get('as_of_month_label') or 'this month'

    # The one sentence that changes. A head past plan being told what the
    # year still needs reads as the system refusing to acknowledge a good
    # month, and that is how a daily mail stops being opened.
    p = h['month_pct']
    if p is None:
        tail = ('There is no AOP against this territory this month, so there '
                'is no achievement figure against it.')
    elif p >= 100:
        tail = 'You are past plan for ' + escape(mon) + '.'
    elif y.get('required_monthly'):
        tail = ('The attached report sets out the year: '
                + lakh(y['required_monthly']) + ' lakh a month is needed across '
                'the ' + str(y['months_remaining']) + ' months left to close the '
                'annual plan.')
    else:
        tail = ''

    intro = ('Please find attached the Subzone-wise and ASM/TSM-wise B2C '
             'Primary Sales Report for the month of <b>' + month_label(snap)
             + '</b> (as of ' + as_of(snap) + '). ' + OT_NOTE)
    html = _shell('Hi ' + escape(first) + ',', intro,
                  summary_table([h], snap.get('as_of_month_label')),
                  '<p style="margin:14px 0 0">' + tail + '</p>' if tail else '')
    return (SUBJECT.format(scope=escape(h['region'] or h['head_name']),
                           asof=as_of(snap)), html)


def for_manager(d):
    """-> (subject, html) for a manager. Their territories and a group line."""
    snap, t = d['snapshot'], d['totals']
    heads = sorted(d['heads'],
                   key=lambda r: (r['month_pct'] is None, -(r['month_pct'] or 0)))
    mon = snap.get('as_of_month_label') or 'this month'
    name = d.get('name') or 'Team'

    behind = [r['region'] for r in heads
              if r['month_pct'] is not None and r['month_pct'] < 70]
    tail = ('Needing attention: <b>' + escape(', '.join(behind)) + '</b>.'
            if behind else 'No territory is below 70% of plan.')

    intro = ('Please find attached the Subzone-wise and ASM/TSM-wise B2C '
             'Primary Sales Report for the month of <b>' + month_label(snap)
             + '</b> (as of ' + as_of(snap) + '), across all '
             + str(len(heads)) + ' of your territories in one report. '
             + OT_NOTE)
    html = _shell('Hi Team,', intro,
                  summary_table(heads, snap.get('as_of_month_label'),
                                totals=t, total_label='Group'),
                  '<p style="margin:14px 0 0">' + tail + '</p>')
    return SUBJECT.format(scope=escape(name), asof=as_of(snap)), html
