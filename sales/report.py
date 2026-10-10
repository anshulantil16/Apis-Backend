"""One head's report, rendered as a standalone HTML file.

Written for the person it is about, not for management reading about them.
That decides most of what follows: figures stay in lakhs because that is the
unit the review sheet is written and read in, so the recipient can check this
line by line against their own copy without doing arithmetic; the terms are
glossed at the foot because not everyone receiving one reads a P&L daily; and
the opening paragraph says the good thing and the hard thing in that order,
because a report that only flatters is one people stop reading and a report
that only scolds is one they resent.

The file is self-contained -- no external CSS, no scripts, no images -- so it
survives being emailed, opened offline, and printed.

This module is the single implementation of the design. The browser preview,
the downloaded file and anything mailed later all render through here, so
there is no second copy to drift.
"""
import re
from decimal import Decimal, ROUND_HALF_UP
from html import escape


# One layout budget for every horizontal bar chart in this file.
#
# Figures used to be printed just after the end of each bar, so where they
# landed depended on the bar's length: a long bar pushed its figure off the
# right edge, and the 100% plan line was drawn straight through the figures
# of the territories near plan. Fixed columns cannot collide -- the bar area
# ends well before the numbers begin, so the reference line has nowhere to
# cross them.
#
# The gutter is deliberately generous. Text is measured by the renderer, not
# by us, and the PDF falls back to different fonts from the browser, so a
# label that just fits on screen can run past the viewBox in print -- where
# there is no scrollbar and it is simply cut off, which is what happened to
# "Average of the first 5 months" and to the handover line's name.
CW = 790.0          # viewBox width; it scales down to the page, never clips
GUT = 272.0         # right edge of the label gutter
BARX = 282.0        # where every bar starts
BARW = 258.0        # the drawing area
VALX = 596.0        # right-aligned headline figure (a percentage)
SUBX = 606.0        # left-aligned supporting figure, smaller and grey


# The month chart on the head's report: same idea, its own widths because
# it carries a tick axis the territory boards do not.
MGUT = 230.0
MBARX = 240.0
MBARW = 330.0
MVALX = 582.0


def fit(text, n):
    """Shorten a label that would otherwise run out of its gutter."""
    t = str(text or '')
    return t if len(t) <= n else t[:n - 1].rstrip() + '…'


# The same colours as literal hex, for use INSIDE an <svg>.
#
# WeasyPrint does not resolve CSS custom properties in SVG: a fill naming
# one is not a colour it understands,
# so it falls back to black. On screen the
# charts were green and orange; printed to the PDF that actually goes out,
# every bar came out black -- and the one chart that mixed literal hex with
# var() printed half its bars green and half black, which is how this was
# finally spotted. Nothing inside an <svg> may use var().
C_PLAN = '#2a78d6'
C_DONE = '#1baf7a'
C_OWED = '#eb6834'
C_SEC = '#eda100'
C_PEER = '#e87ba4'
C_GRID = '#D5DCE6'
C_FAINT = '#EEF2F7'
C_MUTED = '#8A94A6'


def drawable(v):
    """-> a length that can be drawn: never negative, never negative zero.

    A month with more returned than sold is a real thing and the sheet
    writes it as a minus. A negative width is not drawn by any browser, so
    the bar vanishes -- and the territory in the worst trouble is the one
    that disappears off the chart, which is kinder than the truth and
    therefore worse.

    Clamping with max() alone is not enough: max(-0.0, 0) is -0.0 in Python,
    because max returns the first of two equal values, and width="-0.0" is
    not a valid SVG length. A figure that rounds to a whisker below zero is
    exactly how that arises.
    """
    v = v or 0
    return v if v > 0 else 0.0


def round_half_up(v, dp=0):
    """Round the way a spreadsheet does, not the way Python does.

    Python rounds a half to the nearest EVEN digit, so round(48.5) is 48 and
    round(49.5) is 50. Excel rounds a half away from zero, so both go up.
    The review sheet is read in Excel and this report is checked against it
    line by line, so a figure that disagrees in the last digit reads as the
    report being wrong -- and on a percentage, it reads as a whole point.
    """
    if v is None:
        return None
    q = Decimal(1).scaleb(-dp)
    return float(Decimal(repr(float(v))).quantize(q, rounding=ROUND_HALF_UP))

LAKH = 100_000


def _safe(name):
    """A filename that survives Windows, email clients and ZIP listings.

    Everything outside the allowed set is dropped rather than replaced, so a
    region written "GTR04 A/B" cannot put a directory separator -- or a pair
    of dots -- into a path inside the bundle.
    """
    out = re.sub(r'[^A-Za-z0-9 _-]', '', str(name or '')).strip().replace(' ', '_')
    return (out or 'report')[:60]


# -- formatting -----------------------------------------------------------
def lakh(v, dp=2):
    """Rupees in, lakhs out, grouped the Indian way."""
    if v is None:
        return '--'
    n = round(float(v) / LAKH, dp)
    whole, _, frac = f'{abs(n):.{dp}f}'.partition('.')
    if len(whole) > 3:                       # 1122.98 -> 1,122.98
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
            whole = ','.join(parts) + ',' + tail
        else:
            whole = tail
    out = whole + ('.' + frac if dp else '')
    return ('-' if n < 0 else '') + out


def pct(v, dp=0):
    # Rounded half-up before formatting rather than left to the format spec,
    # which rounds half to even and prints 48.5 as "48".
    return '--' if v is None else f'{round_half_up(v, dp):.{dp}f}%'


def signed(v):
    if v is None:
        return '--'
    return ('+' if v > 0 else '') + f'{round_half_up(v):.0f}%'


def ordinal(n):
    if n is None:
        return '--'
    if 10 <= n % 100 <= 20:
        return f'{n}th'
    return f'{n}{ {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th") }'


def rag(p):
    """The same bands the rest of SalesIQ uses. None where there is no plan:
    a territory with no AOP has not passed and has not failed."""
    if p is None:
        return 'none'
    return 'good' if p >= 100 else ('bad' if p < 70 else 'warn')


def e(s):
    return escape(str(s or ''))


# -- small drawing helpers ------------------------------------------------
def bar_row(y, label, width, colour, value, muted=False):
    """One horizontal bar with its value printed at the end of it.

    Every bar is directly labelled rather than relying on colour alone --
    which is also what the palette's contrast warning requires.
    """
    cls = ' class="muted"' if muted else ''
    return (
        f'<text x="{MGUT}" y="{y + 13}" text-anchor="end" class="lab"{cls}>'
        f'{e(label)}</text>'
        f'<rect x="{MBARX}" y="{y}" width="{drawable(width):.1f}" height="18" '
        f'rx="3" fill="{colour}"/>'
        # At a fixed column, not after the bar: printed after it, the longest
        # bar pushed its own figure off the edge of the drawing.
        f'<text x="{MVALX}" y="{y + 13}" class="val">{e(value)}</text>'
    )


CSS = """
:root{
  --paper:#FBFCFD; --card:#FFFFFF; --line:#E3E8EF; --line-2:#F0F3F7;
  --ink:#121826; --ink-2:#4A5568; --ink-3:#8A94A6;
  /* Validated for colourblind separation on this surface. Roles are fixed
     and never reassigned by rank. */
  --plan:#2a78d6; --done:#1baf7a; --owed:#eb6834; --sec:#eda100; --peer:#e87ba4;
  --good:#0f7a44; --warn:#9a6700; --bad:#b4252c;
  --f-display:'Archivo','Segoe UI',system-ui,-apple-system,sans-serif;
  --f-body:'IBM Plex Sans','Segoe UI',system-ui,-apple-system,sans-serif;
  --f-mono:'IBM Plex Mono',ui-monospace,'SFMono-Regular',Consolas,monospace;
}
*{box-sizing:border-box}
html,body{margin:0}
body{background:var(--paper); color:var(--ink); font-family:var(--f-body);
     font-size:15px; line-height:1.55; -webkit-font-smoothing:antialiased}
.wrap{max-width:1020px; margin:0 auto; padding-inline:16px; padding-block:0 64px}
h1,h2,h3{font-family:var(--f-display); margin:0; text-wrap:balance}
.num,.val,td.n{font-family:var(--f-mono); font-variant-numeric:tabular-nums}

/* The masthead carries the colour. Printed, the report was a white sheet
   with a blue chip on it -- correct and characterless, and the first thing
   anybody sees of it. One band does more for how this reads than any amount
   of tinting further down the page, and it costs the charts nothing. */
.band{background:#143C6B;
      background:linear-gradient(118deg,#0E2C50 0%,#1B5A96 58%,#2a78d6 100%);
      color:#FFFFFF; border-bottom:none;
      padding-block:34px 30px; margin-bottom:34px}
.band h1{color:#FFFFFF}
.band .who p{color:#D6E4F5}
.band .code{color:#FFFFFF; background:rgba(255,255,255,.17);
            border-color:rgba(255,255,255,.34)}
.band .stamp{color:#C3D7EE}
.band .stamp b{color:#FFFFFF}
.band .wrap{padding-block:0}
.idrow{display:flex; flex-wrap:wrap; align-items:flex-end; gap:16px 28px}
.code{display:inline-block; font-family:var(--f-display); font-size:12px;
      font-weight:900; letter-spacing:.1em; color:#1b5fae; background:#EAF2FD;
      border:1px solid #CBDFF8; padding:4px 10px; border-radius:5px; margin-bottom:10px}
h1{font-size:clamp(27px,5vw,40px); font-weight:900; line-height:1.04;
   letter-spacing:-.018em}
.who p{margin:7px 0 0; color:var(--ink-2); font-size:13.5px}
.stamp{margin-left:auto; text-align:right; color:var(--ink-3); font-size:12px; line-height:1.6}
.stamp b{display:block; color:var(--ink); font-size:14px; font-weight:700}

.verdict{background:var(--card); border:1px solid var(--line);
         border-left:4px solid var(--done); border-radius:5px;
         padding:20px 22px; margin-bottom:34px}
.verdict p{margin:0; font-size:17px; line-height:1.5}
.verdict p+p{margin-top:11px; font-size:14.5px; color:var(--ink-2)}
.verdict b{font-weight:700}

section{margin-bottom:40px}
.shead{display:flex; flex-wrap:wrap; align-items:baseline; gap:6px 12px;
       padding-bottom:9px; margin-bottom:18px; border-bottom:2px solid var(--ink)}
.shead h2{font-size:18px; font-weight:800}
.shead span{font-size:12.5px; color:var(--ink-3); margin-left:auto}

.grid{display:grid; gap:13px}
.g3{grid-template-columns:repeat(3,1fr)}
.g2{grid-template-columns:repeat(2,1fr)}
@media(max-width:740px){.g3,.g2{grid-template-columns:1fr}}

.tile{background:var(--card); border:1px solid var(--line); border-radius:5px; padding:17px}
.tile .k{font-size:10px; font-weight:800; letter-spacing:.14em;
         text-transform:uppercase; color:var(--ink-3)}
.tile .v{font-family:var(--f-mono); font-size:26px; font-weight:600;
         margin-top:7px; letter-spacing:-.02em}
.tile .s{font-size:12.5px; color:var(--ink-2); margin-top:6px; line-height:1.5}
.tile .s b{color:var(--ink); font-weight:600}

figure{margin:0; background:var(--card); border:1px solid var(--line);
       border-radius:5px; padding:17px}
figcaption{font-size:12.5px; color:var(--ink-2); margin-top:12px; line-height:1.55}
figcaption b{color:var(--ink); font-weight:600}
.scroll{overflow-x:auto}
svg{display:block; max-width:100%; height:auto}
.axis{font-family:var(--f-mono); font-size:10px; fill:var(--ink-3)}
.lab{font-family:var(--f-body); font-size:11.5px; fill:var(--ink-2)}
.lab.muted{fill:var(--ink-3)}
.val{font-family:var(--f-mono); font-size:11.5px; fill:var(--ink); font-weight:500}
.me .lab,.me .val{fill:var(--ink); font-weight:700}

.legend{display:flex; flex-wrap:wrap; gap:6px 18px; margin-bottom:13px}
.legend span{display:inline-flex; align-items:center; gap:7px; font-size:11.5px; color:var(--ink-2)}
.sw{width:11px; height:11px; border-radius:2px; flex:none}
.sw.ring{background:none; border:1.5px dashed var(--owed)}

table{width:100%; border-collapse:collapse; font-size:13.5px; background:var(--card)}
th{font-size:10px; font-weight:800; letter-spacing:.12em; text-transform:uppercase;
   color:var(--ink-3); text-align:right; padding:11px 12px; white-space:nowrap;
   border-bottom:1px solid var(--line)}
th:first-child{text-align:left}
td{padding:10px 12px; border-bottom:1px solid var(--line-2); text-align:right;
   font-family:var(--f-mono); font-variant-numeric:tabular-nums; color:var(--ink-2)}
td:first-child{text-align:left; font-family:var(--f-body); color:var(--ink); font-weight:500}
tr.hl td{background:#F3F8FE; color:var(--ink); font-weight:600}
tr.hl td:first-child{box-shadow:inset 3px 0 0 var(--plan)}
.tbl{border:1px solid var(--line); border-radius:5px; overflow:hidden}

.good{color:var(--good)} .warn{color:var(--warn)} .bad{color:var(--bad)}
.none{color:var(--ink-3)}

ul.pts{margin:0; padding:0; list-style:none}
ul.pts li{position:relative; padding-left:17px; margin-bottom:9px;
          font-size:13.5px; color:var(--ink-2); line-height:1.55}
ul.pts li::before{content:''; position:absolute; left:0; top:.62em; width:6px;
                  height:6px; border-radius:50%; background:var(--plan)}
ul.pts li b{color:var(--ink); font-weight:600}

.note{font-size:12.5px; color:var(--ink-2); line-height:1.6; margin:14px 0 0;
      background:#F6F8FB; border:1px solid var(--line); border-radius:5px; padding:13px 15px}
.note b{color:var(--ink); font-weight:600}

dl.gloss{display:grid; grid-template-columns:150px 1fr; gap:9px 18px; margin:0;
         font-size:13px}
dl.gloss dt{font-weight:700; color:var(--ink)}
dl.gloss dd{margin:0; color:var(--ink-2)}
@media(max-width:620px){dl.gloss{grid-template-columns:1fr; gap:2px}
  dl.gloss dd{margin-bottom:9px}}

footer{border-top:1px solid var(--line); padding-top:17px; font-size:11.5px;
       color:var(--ink-3); line-height:1.65}
@media print{
  body{background:#fff; font-size:10.5pt}
  /* There is no scrollbar on paper. A table wider than the page is not
     scrolled, it is cut off -- which is how the last columns of "Every
     territory" went missing -- so the table is made to fit instead. */
  .scroll{overflow:visible}
  table{font-size:8.6pt; table-layout:fixed; width:100%}
  th,td{padding:5px 5px}
  td:first-child,th:first-child{white-space:nowrap}
  td.nm,th.nm{overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
  /* Without this the band prints as bare text on white, which is exactly
     the version this was meant to stop being. */
  *{-webkit-print-color-adjust:exact; print-color-adjust:exact}
  .band{padding-block:14px}
  section{break-inside:avoid; margin-bottom:22px}
  figure,.tile{break-inside:avoid}
}
"""


def render(d):
    """-> a complete HTML document for one head's report.

    `d` is exactly what SalesReviewReportView returns, so the preview in the
    browser and this file are reading one set of numbers.
    """
    h, y = d['head'], d['year']
    prod, flow, sh = d['productivity'], d['flow'], d['share']
    snap, rank = d['snapshot'], d['rank']
    ct = d.get('channel_total') or {}
    peers = [p for p in d.get('peers', []) if p['month_pct'] is not None]
    peers.sort(key=lambda p: p['month_pct'], reverse=True)

    month = snap.get('as_of_month_label') or 'this month'
    chan = h['channel'] or 'the channel'

    # -- the opening paragraph -------------------------------------------
    #
    # Assembled from what is true of THIS head rather than from a template
    # with the numbers swapped in. A head who is ahead of plan and a head who
    # is behind it need different first sentences, and a report that opens
    # the same way every month stops being read by the third one.
    g_rank = (rank.get('growth_pct') or {}).get('position')
    m_rank = (rank.get('month_pct') or {}).get('position')
    of = (rank.get('month_pct') or {}).get('of')

    if h['month_pct'] is not None and h['month_pct'] >= 100:
        lead = (f"<b>{month} is ahead of target</b> &mdash; {pct(h['month_pct'])} "
                f"done, which is {lakh(-h['month_backlog'])} lakh more than asked for.")
    elif g_rank == 1:
        lead = (f"<b>Your territory is growing fastest in {chan} this month</b> "
                f"&mdash; up {signed(h['growth_pct'])} on last month.")
    elif h['growth_pct'] is not None and h['growth_pct'] > 0:
        lead = (f"<b>{month} is ahead of last month</b> &mdash; "
                f"{signed(h['growth_pct'])}, and {pct(h['month_pct'])} of target "
                f"is done.")
    else:
        lead = (f"<b>{month} is behind target and behind last month</b> &mdash; "
                f"{pct(h['month_pct'])} of target is done, and sales are "
                f"{signed(h['growth_pct'])} on last month.")

    # Short sentences, one point each. The first draft joined all three with
    # semicolons, which nobody reads to the end of at half past eight.
    second = []
    if y.get('prior_pct') is not None and y['prior_months']:
        second.append(f"Earlier months ran at {pct(y['prior_pct'])} of target, so "
                      f"the year is still catching up.")
    if h['month_backlog'] > 0:
        second.append(f"{lakh(h['month_backlog'])} lakh is still to bill this month.")
    if y.get('required_monthly') and y.get('months_remaining'):
        second.append(f"To finish the year you need {lakh(y['required_monthly'])} "
                      f"lakh a month for the next {y['months_remaining']} months. "
                      f"Your best month so far is {lakh(h['mtd_primary'])}.")
    second_html = ' '.join(second)

    # -- gauges ----------------------------------------------------------
    ARC = 245.0        # the semicircle's drawn length

    def gauge(label, value, a, b, colour, caption):
        p = min(max(value or 0, 0), 150) / 150 * ARC
        return f'''<figure>
      <svg viewBox="0 0 200 134" role="img" aria-label="{e(label)}: {pct(value)}">
        <path d="M22 112 A 78 78 0 0 1 178 112" fill="none" stroke="#E8EDF4"
              stroke-width="13" stroke-linecap="round"/>
        <path d="M22 112 A 78 78 0 0 1 178 112" fill="none" stroke="{colour}"
              stroke-width="13" stroke-linecap="round" stroke-dasharray="{p:.1f} {ARC}"/>
        <text x="100" y="97" text-anchor="middle" class="num" fill="#121826"
              style="font-size:36px;font-weight:600">{pct(value)}</text>
        <text x="100" y="124" text-anchor="middle" class="axis"
              style="font-size:10.5px">{e(lakh(a))} of {e(lakh(b))}</text>
      </svg>
      <figcaption>{caption}</figcaption>
    </figure>'''

    TONE = {'good': '#1baf7a', 'warn': '#eda100', 'bad': '#eb6834', 'none': '#8A94A6'}
    gauges = (
        gauge('This month', h['month_pct'], h['mtd_primary'], h['month_target'],
              TONE[rag(h['month_pct'])],
              f"<b>This month, against this month's target.</b> "
              f"{lakh(h['month_backlog'])} lakh still to bill, and the month is not over.")
        + gauge('Year to date', h['ytd_pct'], h['ytd_actual'], h['ytd_target'],
                TONE[rag(h['ytd_pct'])],
                f"<b>April until now, against the target for those months.</b> "
                f"The same measure over {y['months_elapsed']} months instead of one."
                + (f" All of {e(chan)} is at {pct(ct.get('ytd_pct'))}."
                   if ct.get('ytd_pct') is not None else ''))
        + gauge('Full year', h['fy_pct'], h['fy_actual'], h['fy_target'], '#2a78d6',
                "<b>Progress, not a score.</b> Nothing is sold beyond today, so "
                "this stays low until March. It is the number people most often "
                "mistake for a result.")
    )

    # -- the month chart --------------------------------------------------
    # One scale for every bar, chosen so the largest fits with room for its
    # label. Nothing here is drawn to its own scale.
    vals = [y.get('prior_monthly_avg') or 0, h['lmtd'], h['month_target'],
            h['mtd_primary'], y.get('required_monthly') or 0]
    top = max(vals) * 1.1 or 1
    W = MBARW

    def w(v):
        return drawable(float(v or 0)) / top * W

    ticks = ''
    for i in range(5):
        tv = top * i / 4
        x = MBARX + W * i / 4
        ticks += (f'<line x1="{x:.1f}" y1="14" x2="{x:.1f}" y2="186" '
                  f'stroke="{C_FAINT}"/>'
                  f'<text x="{x:.1f}" y="202" text-anchor="middle" class="axis">'
                  f'{lakh(tv, 0)}</text>')

    month_rows = ''
    if y.get('prior_monthly_avg'):
        month_rows += bar_row(20, f"Average of first {y['prior_months']} months",
                              w(y['prior_monthly_avg']), '#A8DCC6',
                              lakh(y['prior_monthly_avg']), muted=True)
    month_rows += bar_row(52, 'Last month, same day', w(h['lmtd']), '#6FC9A3',
                          lakh(h['lmtd']), muted=True)

    # this month: billed, then a 2px gap, then what is still owed
    bw, ow = w(h['mtd_primary']), w(max(h['month_backlog'], 0))
    month_rows += (
        f'<g class="me">'
        f'<text x="{MGUT}" y="97" text-anchor="end" class="lab">{e(month)} so far</text>'
        f'<rect x="{MBARX}" y="84" width="{bw:.1f}" height="20" rx="3" fill="{C_DONE}"/>'
        + (f'<rect x="{MBARX + bw + 2:.1f}" y="84" width="{drawable(ow - 2):.1f}" '
           f'height="20" rx="3" fill="{C_OWED}"/>' if ow > 2 else '')
        + f'<text x="{MVALX}" y="98" class="val">{lakh(h["mtd_primary"])}'
          + (f' &#43; {lakh(h["month_backlog"])} owed' if h['month_backlog'] > 0 else '')
        + f'</text></g>'
        f'<line x1="{MBARX + w(h["month_target"]):.1f}" y1="78" '
        f'x2="{MBARX + w(h["month_target"]):.1f}" y2="118" stroke="{C_PLAN}" '
        f'stroke-width="2"/>'
        f'<text x="{MBARX + w(h["month_target"]):.1f}" y="132" text-anchor="middle" '
        f'class="val" fill="{C_PLAN}">AOP {lakh(h["month_target"])}</text>'
    )
    if y.get('required_monthly'):
        rw = w(y['required_monthly'])
        month_rows += (
            f'<text x="{MGUT}" y="167" text-anchor="end" class="lab">'
            f'Needed each month from here</text>'
            f'<rect x="{MBARX}" y="154" width="{rw:.1f}" height="18" rx="3" '
            f'fill="#FDF0EA" stroke="{C_OWED}" stroke-width="1.5" '
            f'stroke-dasharray="5 4"/>'
            f'<text x="{MVALX}" y="167" class="val" fill="{C_OWED}">'
            f'{lakh(y["required_monthly"])}</text>')

    # -- where this head stands ------------------------------------------
    # A strip of unlabelled marks, not a named league table. The report goes
    # to one person; a board listing eleven colleagues by name and figure
    # hands every reader their peers' numbers, which is somebody else's
    # information and not ours to circulate. What is theirs is where they
    # stand -- so the spread stays, the position stays, the names go. The
    # named table belongs on the manager's report, where the team is the
    # subject.
    SW, SX = 470.0, 190.0
    vals = sorted(drawable(p['month_pct']) for p in peers)
    smax = max(vals + [100]) * 1.12 or 100
    def sx(v):
        return SX + drawable(v) / smax * SW

    marks = ''
    for p in peers:
        if p['id'] == h['id']:
            continue
        marks += (f'<circle cx="{sx(p["month_pct"]):.1f}" cy="40" r="5.5" '
                  f'fill="{C_PEER}" opacity=".5"/>')
    mine_pct = h['month_pct'] or 0
    mine_x = sx(mine_pct)
    # The median rather than the mean: one territory at 139% drags an average
    # above most of the people it is meant to describe.
    mid = vals[len(vals) // 2] if vals else 0
    strip_h = 108
    # -- the year ahead ---------------------------------------------------
    year_pts = []
    if y.get('plan_due_by_now_pct') is not None:
        year_pts.append(f"<b>{pct(y['plan_due_by_now_pct'])} of the yearly target "
                        f"was due by now</b> &mdash; {lakh(h['ytd_target'])} of "
                        f"{lakh(h['fy_target'])} lakh.")
    if h['fy_pct'] is not None:
        year_pts.append(f"<b>{pct(h['fy_pct'])} is done so far</b> &mdash; "
                        f"{lakh(h['fy_actual'])} lakh.")
    if y.get('plan_ahead') is not None and y.get('months_remaining'):
        year_pts.append(
            f"<b>The {lakh(h['fy_backlog'])} lakh yearly backlog is made of two "
            f"parts.</b> {lakh(y['plan_ahead'])} is the normal target for the next "
            f"{y['months_remaining']} months. {lakh(y['catch_up'])} is catching up "
            f"on months already gone.")
        year_pts.append(
            f"<b>{lakh(y['required_base'])} lakh a month just to stay on target</b>, "
            f"or <b>{lakh(y['required_monthly'])} to also clear the shortfall</b>.")
    if y.get('required_vs_current'):
        year_pts.append(f"That is {y['required_vs_current']} times your best month "
                        f"so far, and {y['required_vs_month_target']} times this "
                        f"month's target.")

    # Plan-against-achieved, three horizons, each filled left to right.
    def split(done, target, label, yy):
        t = float(target or 0)
        if t <= 0:
            return (f'<text x="0" y="{yy}" class="lab">{e(label)} &mdash; no plan</text>'
                    f'<rect x="0" y="{yy + 8}" width="420" height="18" rx="3" fill="#F0F3F7"/>')
        dw = min(max(float(done) / t, 0), 1) * 420
        out = (f'<text x="0" y="{yy}" class="lab">{e(label)} &mdash; plan '
               f'{lakh(target)}</text>'
               f'<rect x="0" y="{yy + 8}" width="{dw:.1f}" height="18" rx="3" fill="{C_DONE}"/>')
        if dw < 418:
            out += (f'<rect x="{dw + 2:.1f}" y="{yy + 8}" width="{418 - dw:.1f}" '
                    f'height="18" rx="3" fill="{C_OWED}"/>')
        # Inside the bar while there is room for it, after it when there is
        # not. A figure printed over the join of two fills is unreadable.
        if dw >= 58:
            out += (f'<text x="8" y="{yy + 21}" class="val" '
                    f'fill="#06281B">{lakh(done)}</text>')
        else:
            out += (f'<text x="{dw + 8:.1f}" y="{yy + 21}" class="val" '
                    f'fill="#2B1710">{lakh(done)}</text>')
        if dw < 360:
            out += (f'<text x="412" y="{yy + 21}" text-anchor="end" class="val" '
                    f'fill="#2B1710">{lakh(t - float(done))}</text>')
        return out

    horizons = (split(h['mtd_primary'], h['month_target'], 'This month', 14)
                + split(h['ytd_actual'], h['ytd_target'], 'April to date', 82)
                + split(h['fy_actual'], h['fy_target'], 'Full year', 150))

    # -- productivity -----------------------------------------------------
    def vs(mine, avg):
        if not mine or not avg:
            return '', 'none'
        diff = (mine / avg - 1) * 100
        tone = 'good' if diff >= 0 else 'warn'
        return f"{signed(diff)} {'above' if diff >= 0 else 'below'}", tone

    pm, pm_tone = vs(prod.get('per_sfo_mtd'), prod.get('channel_per_sfo_mtd'))
    py, py_tone = vs(prod.get('per_sfo_ytd'), prod.get('channel_per_sfo_ytd'))

    sfo_block = ''
    if prod.get('sfo_count'):
        sfo_block = f'''
  <section>
    <div class="shead"><h2>Your {prod['sfo_count']} officers</h2>
      <span>Sales per officer</span></div>
    <div class="grid g3">
      <div class="tile"><div class="k">Per officer, this month</div>
        <div class="v {pm_tone}">{lakh(prod['per_sfo_mtd'])}</div>
        <div class="s">Against <b>{lakh(prod['channel_per_sfo_mtd'])}</b> across
          {e(chan)}. <b class="{pm_tone}">{pm}</b> the channel average.</div></div>
      <div class="tile"><div class="k">Per officer, this year</div>
        <div class="v {py_tone}">{lakh(prod['per_sfo_ytd'])}</div>
        <div class="s">Against <b>{lakh(prod['channel_per_sfo_ytd'])}</b> across
          {e(chan)}. <b class="{py_tone}">{py}</b>.</div></div>
      <div class="tile"><div class="k">Share of {e(chan)}</div>
        <div class="v">{pct(sh.get('of_channel_mtd'), 1)}</div>
        <div class="s">This month, against <b>{pct(sh.get('of_channel_ytd'), 1)}</b>
          of its year to date.</div></div>
    </div>
    <p class="note"><b>Read the first two together.</b> If this month looks
      different from the year, something has changed recently.</p>
  </section>'''

    days = flow.get('days_to_clear')
    sec = flow.get('secondary_to_primary')

    return f'''<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{e(h['head_name'])} &mdash; {e(month)}</title>
<style>{CSS}</style>
</head><body>

<div class="band"><div class="wrap"><div class="idrow">
  <div class="who">
    <span class="code">{e(h['region'])} &#183; {e(h['channel'])}</span>
    <h1>{e(h['head_name'])}</h1>
    <p>{e(h['head_code']) or 'ID not matched'}
      {f"&#183; {prod['sfo_count']} sales field officers" if prod.get('sfo_count') else ''}</p>
  </div>
  <div class="stamp"><b>{e(month)}</b>
    Month to date{f" &#183; as at {e(snap['as_of_date'])}" if snap.get('as_of_date') else ''}<br>
    Figures in &#8377; lakhs &#183; primary sales basis
  </div>
</div></div></div>

<div class="wrap">

  <div class="verdict">
    <p>{lead}</p>
    {f'<p>{second_html}</p>' if second_html else ''}
  </div>

  <section>
    <div class="shead"><h2>Your three targets</h2>
      <span>This month, this year, and the full year</span></div>
    <div class="grid g3">{gauges}</div>
  </section>

  <section>
    <div class="shead"><h2>{e(month)}</h2>
      <span>How this month is going</span></div>
    <div class="grid g3" style="margin-bottom:13px">
      <div class="tile"><div class="k">Growth over last month</div>
        <div class="v {'good' if (h['growth_pct'] or 0) > 0 else 'bad'}">
          {signed(h['growth_pct'])}</div>
        <div class="s">{lakh(h['mtd_primary'])} against <b>{lakh(h['lmtd'])}</b> at the
          same point last month{f". Ranked {ordinal(g_rank)} of {of} in {e(chan)}" if g_rank else ''}.</div></div>
      <div class="tile"><div class="k">Still owed this month</div>
        <div class="v {'owed' if h['month_backlog'] > 0 else 'good'}"
             style="color:{'var(--owed)' if h['month_backlog'] > 0 else 'var(--good)'}">
          {lakh(h['month_backlog'])}</div>
        <div class="s">{f"At yesterday&#8217;s billing of {lakh(flow['yesterday_billing'])}, that is <b>{days} more days</b> at the same rate." if days else "Ahead of this month&#8217;s plan already."}</div></div>
      <div class="tile"><div class="k">Secondary against primary</div>
        <div class="v">{pct(sec)}</div>
        <div class="s">{lakh(h['mtd_secondary'])} moved out of distributors against
          {lakh(h['mtd_primary'])} moved in.
          {f"{e(chan)} runs at <b>{pct(flow['channel_secondary_to_primary'])}</b>." if flow.get('channel_secondary_to_primary') else ''}</div></div>
    </div>

    <figure>
      <div class="legend">
        <span><i class="sw" style="background:var(--done)"></i>Billed</span>
        <span><i class="sw" style="background:var(--owed)"></i>Still to bill</span>
        <span><i class="sw" style="background:var(--plan)"></i>AOP</span>
        <span><i class="sw ring"></i>Needed from here</span>
      </div>
      <svg viewBox="0 0 {CW} 212" role="img"
        aria-label="Monthly billing against plan and against what the rest of the year needs">
        {ticks}
        <line x1="{MBARX}" y1="14" x2="{MBARX}" y2="186" stroke="{C_GRID}"/>
        {month_rows}
      </svg>
      <figcaption>All bars use the same scale, in lakhs. The dashed bar is
        what you need each month to finish the year.</figcaption>
    </figure>
  </section>

  <section>
    <div class="shead"><h2>The year ahead</h2>
      <span>{y['months_elapsed']} months gone, {y['months_remaining']} to go</span></div>
    <div class="grid g2">
      <div>
        <ul class="pts">{''.join(f'<li>{p}</li>' for p in year_pts)}</ul>
        <p class="note"><b>Why we split this.</b> The sheet shows one yearly
          backlog. Split in two, you can see how much is left over from past
          months and how much is still to come.</p>
      </div>
      <figure>
        <div class="legend">
          <span><i class="sw" style="background:var(--done)"></i>Billed</span>
          <span><i class="sw" style="background:var(--owed)"></i>Backlog</span>
        </div>
        <svg viewBox="0 0 420 200" role="img"
             aria-label="Target against achievement over the three periods">
          {horizons}
        </svg>
        <figcaption>Each bar is one target, filled to show how much is
          done.</figcaption>
      </figure>
    </div>
  </section>
{sfo_block}
  <section>
    <div class="shead"><h2>How you compare</h2>
      <span>Against the other {of} heads in {e(chan)}</span></div>
    <figure>
      <svg viewBox="0 0 {CW} {strip_h}" role="img"
        aria-label="This territory's achievement against the spread of the channel">
        <line x1="{SX}" y1="40" x2="{SX + SW}" y2="40" stroke="#E3E8EF"
              stroke-width="2"/>
        <line x1="{sx(100):.1f}" y1="18" x2="{sx(100):.1f}" y2="62"
              stroke="{C_PLAN}" stroke-width="1.5"/>
        <text x="{sx(100):.1f}" y="78" text-anchor="middle" class="axis"
              fill="{C_PLAN}">100% &mdash; plan</text>
        <line x1="{sx(mid):.1f}" y1="24" x2="{sx(mid):.1f}" y2="56"
              stroke="#9AA6B8" stroke-dasharray="3 3"/>
        <text x="{sx(mid):.1f}" y="96" text-anchor="middle" class="axis">
          {pct(mid)} &mdash; middle of {e(chan)}</text>
        {marks}
        <circle cx="{mine_x:.1f}" cy="40" r="9" fill="{C_DONE}"/>
        <text x="{SX - 12:.1f}" y="45" text-anchor="end" class="lab"><tspan
          font-weight="700">You</tspan> &#183; {e(h['region'])}</text>
        <text x="{mine_x:.1f}" y="22" text-anchor="middle" class="val"
              fill="{C_DONE}" font-weight="700">{pct(h['month_pct'])}</text>
      </svg>
      <figcaption>Each faint dot is another territory in {e(chan)}. Names
        are hidden. You are {ordinal(m_rank)} of {of} this month{
        f", {ordinal((rank.get('ytd_pct') or {}).get('position'))} of {of} for the year" if rank.get('ytd_pct') else ''}{
        f", and <b>{ordinal(g_rank)} of {of} on growth</b>" if g_rank else ''}.
        We compare inside {e(chan)} only, because other channels have a
        different team size and a different plan.</figcaption>
    </figure>
  </section>

  <section>
    <div class="shead"><h2>Your numbers</h2>
      <span>As shown on the sheet, in &#8377; lakhs</span></div>
    <div class="tbl"><table>
      <thead><tr><th>Period</th><th>AOP</th><th>Achieved</th><th>ACH %</th>
        <th>Backlog</th></tr></thead>
      <tbody>
        <tr class="hl"><td>{e(month)}, month to date</td><td class="n">{lakh(h['month_target'])}</td>
          <td class="n">{lakh(h['mtd_primary'])}</td>
          <td class="n {rag(h['month_pct'])}">{pct(h['month_pct'])}</td>
          <td class="n">{lakh(h['month_backlog'])}</td></tr>
        <tr><td>This year, from April</td><td class="n">{lakh(h['ytd_target'])}</td>
          <td class="n">{lakh(h['ytd_actual'])}</td>
          <td class="n {rag(h['ytd_pct'])}">{pct(h['ytd_pct'])}</td>
          <td class="n">{lakh(h['ytd_backlog'])}</td></tr>
        <tr><td>Full year</td><td class="n">{lakh(h['fy_target'])}</td>
          <td class="n">{lakh(h['fy_actual'])}</td>
          <td class="n none">{pct(h['fy_pct'])}</td>
          <td class="n">{lakh(h['fy_backlog'])}</td></tr>
      </tbody>
    </table></div>
    <p class="note"><b>Other figures.</b>
      {f"Officers {prod['sfo_count']} &#183; " if prod.get('sfo_count') else ''}
      LMTD {lakh(h['lmtd'])} &#183; yesterday&#8217;s billing
      {lakh(flow['yesterday_billing'])} &#183; MTD secondary sales
      {lakh(h['mtd_secondary'])} &#183; growth over last month {signed(h['growth_pct'])}.</p>
  </section>

  <section>
    <div class="shead"><h2>What these words mean</h2>
      <span>A quick guide</span></div>
    <dl class="gloss">
      <dt>AOP</dt><dd>The target for the period, set at the start of the year.</dd>
      <dt>Primary sales</dt><dd>Sold by the company to the distributor. Your
        achievement is measured on this.</dd>
      <dt>Secondary sales</dt><dd>Sold by the distributor to the shop. Shown next
        to primary, but not scored. Lower than primary means stock is building up
        with the distributor. Higher means they are clearing old stock.</dd>
      <dt>LMTD</dt><dd>Last month up to the same day, so the comparison is fair.</dd>
      <dt>MTD / YTD</dt><dd>This month so far, and this year so far. Our year starts
        in April.</dd>
      <dt>Backlog</dt><dd>Target minus what you have billed. A minus figure means you
        are ahead of plan.</dd>
      <dt>FY ACH %</dt><dd>How much of the full-year plan is done. This grows all
        year, so a small number early on is normal.</dd>
    </dl>
  </section>

  <footer>
    Made from the {e(month)} review sheet{
      f", uploaded on {e(snap['created_at'][:10])}" if snap.get('created_at') else ''}.
    Percentages and backlogs are worked out again from the AOP and sales columns,
    so they may differ from the sheet by up to a paisa. Achievement is measured on
    primary sales. All figures in &#8377; lakhs.
  </footer>
</div>
</body></html>'''


# -- the manager's rolled-up report ---------------------------------------
#
# A different document, not the head report with more rows in it. A head is
# being asked about their own month; a manager is being asked which of their
# territories needs attention this week. So this leads with the comparison
# between them, where the head report leads with one territory and keeps the
# comparison to a chart near the bottom.
def render_team(d):
    """-> a complete HTML document for a manager's rolled-up report.

    A different document, not the head report with more rows in it. A head is
    being asked about their own month; a manager is being asked which of
    their territories needs attention this week, and where the shortfall
    actually sits. So this leads with the comparison between territories,
    where the head report leads with one territory and keeps the comparison
    to a strip near the bottom.

    Three charts, each answering a different question, because one bar chart
    of achievement answers only the first of them:

      * which territories are behind plan  -- the achievement board;
      * where the money is                 -- the shortfall in lakhs, since a
        territory 20% behind on a large plan is a bigger problem than one 40%
        behind on a small one, and a percentage cannot say that;
      * which way they are moving          -- achievement against growth, so
        a territory that is behind but climbing is not read the same as one
        that is behind and falling.
    """
    t, y = d['totals'], d['year']
    snap = d['snapshot']
    # Sorted by achievement rather than by region code, so the ones that need
    # attention sit together instead of being scattered through the list.
    # Territories with no plan go last: they have not come bottom, they have
    # not been measured.
    heads = sorted(d['heads'],
                   key=lambda h: (h['month_pct'] is None, -(h['month_pct'] or 0)))
    month = snap.get('as_of_month_label') or 'this month'
    name = d.get('name') or 'Team'
    n = len(heads)

    behind = [h for h in heads if h['month_pct'] is not None and h['month_pct'] < 70]
    ahead = [h for h in heads if h['month_pct'] is not None and h['month_pct'] >= 100]

    TONE = {'good': '#1baf7a', 'warn': '#eda100', 'bad': '#eb6834',
            'none': '#8A94A6'}

    # -- the group at a glance --------------------------------------------
    ARC = 245.0

    def gauge(label, value, a, b, colour, caption):
        p = min(max(value or 0, 0), 150) / 150 * ARC
        return f'''<figure>
      <svg viewBox="0 0 200 134" role="img" aria-label="{e(label)}: {pct(value)}">
        <path d="M22 112 A 78 78 0 0 1 178 112" fill="none" stroke="#E8EDF4"
              stroke-width="13" stroke-linecap="round"/>
        <path d="M22 112 A 78 78 0 0 1 178 112" fill="none" stroke="{colour}"
              stroke-width="13" stroke-linecap="round" stroke-dasharray="{p:.1f} {ARC}"/>
        <text x="100" y="97" text-anchor="middle" class="num" fill="#121826"
              style="font-size:36px;font-weight:600">{pct(value)}</text>
        <text x="100" y="124" text-anchor="middle" class="axis"
              style="font-size:10.5px">{e(lakh(a))} of {e(lakh(b))}</text>
      </svg>
      <figcaption>{caption}</figcaption>
    </figure>'''

    gauges = (
        gauge('This month', t['month_pct'], t['mtd_primary'], t['month_target'],
              TONE[rag(t['month_pct'])],
              f"<b>This month, against this month&#8217;s target.</b> "
              f"{lakh(t['month_backlog'])} lakh still to bill across the group, "
              f"and the month is not over.")
        + gauge('April to date', t['ytd_pct'], t['ytd_actual'], t['ytd_target'],
                TONE[rag(t['ytd_pct'])],
                f"<b>April until now, against the target for those months.</b> "
                f"The same measure over {y['months_elapsed']} months instead "
                f"of one.")
        + gauge('Full year', t['fy_pct'], t['fy_actual'], t['fy_target'],
                '#2a78d6',
                "<b>Progress, not a score.</b> Nothing is sold beyond today, so "
                "this stays low until March. It is the figure people most often "
                "mistake for a result."))

    # -- board 1: achievement ---------------------------------------------
    board, Y = '', 12
    top = max([h['month_pct'] or 0 for h in heads] + [100]) * 1.04
    for h in heads:
        p = h['month_pct']
        label = (f'<text x="{GUT}" y="{Y + 13}" text-anchor="end" class="lab">'
                 f'{e(fit(h["region"] + " · " + h["head_name"], 30))}</text>')
        if p is None:
            # In the figure column, not across the drawing area: printed
            # from the bar's own origin it ran straight through the 100%
            # plan line.
            board += label + (f'<text x="{VALX}" y="{Y + 13}" '
                              f'text-anchor="end" class="val" '
                              f'fill="{C_MUTED}">--</text>'
                              f'<text x="{SUBX}" y="{Y + 13}" class="val" '
                              f'fill="{C_MUTED}" style="font-size:10px">'
                              f'no plan this month</text>')
        else:
            w = drawable(p) / top * BARW
            board += label + (
                f'<rect x="{BARX}" y="{Y}" width="{w:.1f}" height="18" rx="3" '
                f'fill="{TONE[rag(p)]}"/>'
                f'<text x="{VALX}" y="{Y + 13}" text-anchor="end" class="val">'
                f'{pct(p)}</text>'
                f'<text x="{SUBX}" y="{Y + 13}" class="val" fill="{C_MUTED}" '
                f'style="font-size:10px">{lakh(h["mtd_primary"])} of '
                f'{lakh(h["month_target"])}</text>')
        Y += 26
    plan_x = BARX + 100 / top * BARW
    board_h = Y + 30

    # -- board 2: where the shortfall sits --------------------------------
    #
    # In lakhs, not percent. A territory 20% behind on a plan of 400 is a
    # bigger hole than one 40% behind on a plan of 90, and the achievement
    # board above cannot say so -- it is the same chart every month telling
    # a manager to go and talk to the smallest territory they have.
    owed = sorted((h for h in heads if h['month_target']),
                  key=lambda h: -(h['month_backlog'] or 0))
    gap, G = '', 12
    span = max([drawable(h['mtd_primary']) + drawable(h['month_backlog'])
                for h in owed] + [1])
    GAPW = 232.0          # narrower: this one also prints a sentence on the right
    for h in owed:
        b = h['month_backlog'] or 0
        billed = h['mtd_primary'] or 0
        bw = drawable(min(billed, span)) / span * GAPW
        gap += (f'<text x="{GUT}" y="{G + 13}" text-anchor="end" class="lab">'
                f'{e(fit(h["region"] + " · " + h["head_name"], 30))}</text>'
                f'<rect x="{BARX}" y="{G}" width="{bw:.1f}" height="18" rx="3" '
                f'fill="{C_DONE}" opacity=".9"/>')
        if b > 0:
            ow = drawable(min(b, span)) / span * GAPW
            gap += (f'<rect x="{BARX + bw:.1f}" y="{G}" width="{ow:.1f}" '
                    f'height="18" rx="3" fill="{C_OWED}"/>'
                    f'<text x="{VALX}" y="{G + 13}" text-anchor="end" '
                    f'class="val" fill="#8f3c12">{lakh(b)}</text>'
                    f'<text x="{SUBX}" y="{G + 13}" class="val" '
                    f'fill="{C_MUTED}" style="font-size:10px">still owed</text>')
        else:
            gap += (f'<text x="{VALX}" y="{G + 13}" text-anchor="end" '
                    f'class="val" fill="#0f7a44">{lakh(-b)}</text>'
                    f'<text x="{SUBX}" y="{G + 13}" class="val" '
                    f'fill="{C_MUTED}" style="font-size:10px">past plan</text>')
        G += 26
    gap_h = G + 14
    total_owed = sum(drawable(h['month_backlog']) for h in owed)
    worst = owed[0] if owed and (owed[0]['month_backlog'] or 0) > 0 else None
    worst_share = (round_half_up((worst['month_backlog'] or 0) / total_owed * 100)
                   if worst and total_owed else None)

    # -- board 3: achievement against growth ------------------------------
    #
    # Thirteen dots in a box is a crowd. The labels are placed one at a time
    # against the ones already down, trying above the dot, then below, then
    # to either side -- a label printed on top of another says nothing about
    # either, and the first draft of this had four of them overlapping.
    plotted = [h for h in heads
               if h['month_pct'] is not None and h['growth_pct'] is not None]
    SX0, SX1, SY0, SY1 = 108.0, 700.0, 42.0, 376.0
    xs = [h['month_pct'] for h in plotted] + [100]
    ys = [h['growth_pct'] for h in plotted] + [0]
    xlo, xhi = min(xs + [0]), max(xs)
    ylo, yhi = min(ys), max(ys)
    xpad, ypad = max((xhi - xlo) * .1, 8), max((yhi - ylo) * .16, 10)
    xlo, xhi = xlo - xpad, xhi + xpad
    ylo, yhi = ylo - ypad, yhi + ypad

    def px(v):
        return SX0 + (v - xlo) / (xhi - xlo) * (SX1 - SX0)

    def py(v):
        return SY1 - (v - ylo) / (yhi - ylo) * (SY1 - SY0)

    placed, dots, marks = [], '', []
    for h in sorted(plotted, key=lambda r: -(r['month_pct'] or 0)):
        marks.append((px(h['month_pct']), py(h['growth_pct']), h))
    # Every dot is an obstacle before any label is placed. Checking labels
    # only against each other left two of them sitting on top of somebody
    # else's dot, which hides the very mark the label is there to name.
    for x, yy, _ in marks:
        placed.append((x - 8, yy - 8, x + 8, yy + 8))
    for x, yy, h in marks:
        dots += (f'<circle cx="{x:.1f}" cy="{yy:.1f}" r="6" '
                 f'fill="{TONE[rag(h["month_pct"])]}" stroke="#FFFFFF" '
                 f'stroke-width="1.5"/>')
    for x, yy, h in marks:
        text = h['region']
        w = len(text) * 5.6 + 4
        for dx, dy, anchor in ((0, -12, 'middle'), (0, 19, 'middle'),
                               (11, 4, 'start'), (-11, 4, 'end'),
                               (0, -24, 'middle'), (0, 31, 'middle')):
            cx = x + dx
            x0 = cx - (w / 2 if anchor == 'middle'
                       else 0 if anchor == 'start' else w)
            box = (x0, yy + dy - 9, x0 + w, yy + dy + 3)
            if box[0] < SX0 - 34 or box[2] > SX1 + 2:
                continue
            if any(not (box[2] < o[0] or box[0] > o[2]
                        or box[3] < o[1] or box[1] > o[3]) for o in placed):
                continue
            placed.append(box)
            dots += (f'<text x="{cx:.1f}" y="{yy + dy:.1f}" '
                     f'text-anchor="{anchor}" class="lab" '
                     f'style="font-size:10px">{e(text)}</text>')
            break
    qx, qy = px(100), py(0)
    rising_behind = [h for h in plotted
                     if h['month_pct'] < 100 and h['growth_pct'] > 0]
    falling_behind = [h for h in plotted
                      if h['month_pct'] < 100 and h['growth_pct'] <= 0]


    def tile(k, v, s, tone=''):
        return (f'<div class="tile"><div class="k">{k}</div>'
                f'<div class="v {tone}">{v}</div><div class="s">{s}</div></div>')

    # -- the table --------------------------------------------------------
    rows = ''
    tmax = max([h['month_pct'] or 0 for h in heads] + [100]) * 1.05
    for h in heads:
        p = h['month_pct']
        # A bar behind the figure, so the column can be read down at a glance
        # instead of thirteen numbers being compared in the head.
        bar = ''
        if p is not None:
            bar = (f'<span class="minib">'
                   f'<i style="width:{drawable(p) / tmax * 100:.1f}%;'
                   f'background:{TONE[rag(p)]}"></i></span>')
        rows += (
            '<tr>'
            f'<td>{e(h["region"])}</td>'
            f'<td class="nm">{e(h["head_name"])}</td>'
            f'<td class="n">{lakh(h["month_target"])}</td>'
            f'<td class="n">{lakh(h["mtd_primary"])}</td>'
            f'<td class="n {rag(p)}"><b>{pct(p)}</b>{bar}</td>'
            f'<td class="n {"good" if (h["growth_pct"] or 0) > 0 else "bad"}">'
            f'{signed(h["growth_pct"])}</td>'
            f'<td class="n">{lakh(h["month_backlog"])}</td>'
            f'<td class="n {rag(h["ytd_pct"])}">{pct(h["ytd_pct"])}</td>'
            f'<td class="n">{lakh(h["fy_backlog"])}</td>'
            '</tr>')

    regions = ', '.join(d['regions'][:6]) + ('...' if len(d['regions']) > 6 else '')
    asat = (f" &#183; as at {e(snap['as_of_date'])}" if snap.get('as_of_date') else '')
    ahead_line = (f"{len(ahead)} of {n} "
                  f"{'territory is' if len(ahead) == 1 else 'territories are'} "
                  f"at or above plan. " if ahead else '')
    behind_line = ('<b>' + str(len(behind)) + ' need attention</b>: '
                   + e(', '.join(h['region'] for h in behind)) + '. ') if behind else ''

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{e(name)} &mdash; {e(month)}</title>
<style>{CSS}
th.nm,td.nm{{text-align:left}}
td.nm{{font-family:var(--f-body); color:var(--ink-2); font-weight:400}}
tfoot td{{border-top:2px solid var(--ink); border-bottom:none}}
.minib{{display:block; height:3px; margin-top:4px; background:var(--line-2);
        border-radius:2px; overflow:hidden}}
.minib i{{display:block; height:100%}}
/* Nine columns on A4. Left to themselves they divide evenly, the head name
   takes as much as the figures, and the last columns fall off the page. */
.terr col.r{{width:8.5%}} .terr col.h{{width:17.5%}} .terr col.n{{width:10.2%}}
/* Data never wraps; headings may. "FY BACKLOG" on one line was a shade
   wider than its column and lost its last letters off the page. */
.terr td{{white-space:nowrap}}
.terr th{{white-space:normal; line-height:1.25}}
</style>
</head><body>

<div class="band"><div class="wrap"><div class="idrow">
  <div class="who">
    <span class="code">{n} territories &#183; {e(regions)}</span>
    <h1>{e(name)}</h1>
    <p>{t['sfo_count']} sales field officers across the group</p>
  </div>
  <div class="stamp"><b>{e(month)}</b>
    Month to date{asat}<br>
    Figures in &#8377; lakhs &#183; primary sales basis
  </div>
</div></div></div>

<div class="wrap">
  <div class="verdict">
    <p><b>The group is at {pct(t['month_pct'])} of plan this month</b>,
      {signed(t['growth_pct'])} on last month, with
      {lakh(t['month_backlog'])} lakh still to bill.</p>
    <p>{ahead_line}{behind_line}The year so far is at {pct(t['ytd_pct'])}, and
      {lakh(t['fy_backlog'])} lakh is left to do over
      {y['months_remaining']} months &mdash; {lakh(y['required_monthly'])} a month.</p>
  </div>

  <section>
    <div class="shead"><h2>Your group</h2>
      <span>This month, this year, and the full year</span></div>
    <div class="grid g3">{gauges}</div>
    <p class="note"><b>These totals are added up from your territories only.</b>
      They are not taken from the grand total on the sheet, which covers people
      outside your group.</p>
  </section>

  <section>
    <div class="shead"><h2>How each territory is doing</h2>
      <span>This month against plan</span></div>
    <figure>
      <svg viewBox="0 0 {CW} {board_h}" role="img"
        aria-label="Achievement against AOP this month, by territory">
        <line x1="{BARX}" y1="6" x2="{BARX}" y2="{Y - 4}" stroke="{C_GRID}"/>
        <line x1="{plan_x:.1f}" y1="6" x2="{plan_x:.1f}" y2="{Y - 4}"
              stroke="{C_PLAN}" stroke-width="1.5"/>
        <text x="{plan_x:.1f}" y="{Y + 18}" text-anchor="middle" class="axis"
              fill="{C_PLAN}">100% &mdash; plan</text>
        {board}
      </svg>
      <figcaption>Sorted by achievement, so the ones needing attention sit
        together at the bottom. Billed and planned are shown beside each bar,
        because two territories at 74% can be very different in size.</figcaption>
    </figure>
  </section>

  <section>
    <div class="shead"><h2>Where the gap is</h2>
      <span>In &#8377; lakhs, biggest first</span></div>
    <figure>
      <div class="legend">
        <span><i class="sw" style="background:var(--done)"></i>Billed</span>
        <span><i class="sw" style="background:var(--owed)"></i>Still to bill this month</span>
      </div>
      <svg viewBox="0 0 {CW} {gap_h}" role="img"
        aria-label="Billed and still owed this month, by territory, in lakhs">
        <line x1="{BARX}" y1="6" x2="{BARX}" y2="{G - 4}" stroke="{C_GRID}"/>
        {gap}
      </svg>
      <figcaption>The same month as above, but in money instead of percent.
        A small gap on a big territory can matter more than a large gap on a
        small one.{
        f" Of the {lakh(total_owed)} lakh still to bill across the group, {worst_share:.0f}% is in {e(worst['region'])} alone."
        if worst_share is not None else ''}</figcaption>
    </figure>
  </section>
{f'''
  <section>
    <div class="shead"><h2>Which way each is moving</h2>
      <span>Plan against growth</span></div>
    <figure>
      <svg viewBox="0 0 {CW} 410" role="img"
        aria-label="Achievement against growth, by territory">
        <rect x="{SX0}" y="{SY0}" width="{SX1 - SX0}" height="{SY1 - SY0}"
              fill="#FBFCFD" stroke="{C_FAINT}"/>
        <line x1="{qx:.1f}" y1="{SY0}" x2="{qx:.1f}" y2="{SY1}"
              stroke="{C_PLAN}" stroke-width="1.5"/>
        <line x1="{SX0}" y1="{qy:.1f}" x2="{SX1}" y2="{qy:.1f}"
              stroke="#9AA6B8" stroke-dasharray="4 4"/>
        <text x="{SX1 - 8:.1f}" y="{SY0 + 17:.1f}" text-anchor="end"
              class="axis" fill="#0f7a44">past plan and growing</text>
        <text x="{SX0 + 8:.1f}" y="{SY1 - 9:.1f}" class="axis"
              fill="#b4252c">behind plan and falling</text>
        <text x="{qx:.1f}" y="{SY1 + 24:.1f}" text-anchor="middle" class="axis"
              fill="{C_PLAN}">100% of plan</text>
        <text x="{SX0 - 10:.1f}" y="{qy + 4:.1f}" text-anchor="end"
              class="axis">level with</text>
        <text x="{SX0 - 10:.1f}" y="{qy + 16:.1f}" text-anchor="end"
              class="axis">last month</text>
        {dots}
      </svg>
      <figcaption>Left to right: how much of this month&#8217;s plan is done.
        Up and down: growth over last month. A territory behind plan but
        growing is a different problem from one behind and falling.
        {len(rising_behind)} of {n} are behind plan but ahead of last month;
        {len(falling_behind)} are behind on both.</figcaption>
    </figure>
  </section>''' if len(plotted) >= 3 else ''}

  <section>
    <div class="shead"><h2>The year ahead</h2>
      <span>{y['months_elapsed']} months gone, {y['months_remaining']} to go</span></div>
    <div class="grid g3">
      {tile('April to date', pct(t['ytd_pct']),
            f"{lakh(t['ytd_actual'])} against {lakh(t['ytd_target'])}. "
            f"{lakh(t['ytd_backlog'])} behind.", rag(t['ytd_pct']))}
      {tile('Annual backlog', lakh(t['fy_backlog']),
            f"Of a {lakh(t['fy_target'])} lakh plan, {lakh(t['fy_actual'])} "
            f"is done.")}
      {tile('Needed each month', lakh(y['required_monthly']),
            f"To close it across the {y['months_remaining']} months left. "
            f"This month&#8217;s plan is {lakh(t['month_target'])}.")}
    </div>
  </section>

  <section>
    <div class="shead"><h2>Every territory</h2>
      <span>Exactly as circulated, in &#8377; lakhs</span></div>
    <div class="scroll"><div class="tbl"><table class="terr">
      <colgroup><col class="r"><col class="h"><col class="n"><col class="n">
        <col class="n"><col class="n"><col class="n"><col class="n">
        <col class="n"></colgroup>
      <thead><tr><th>Region</th><th class="nm">Head</th><th>AOP</th><th>Billed</th>
        <th>ACH</th><th>vs LM</th><th>Backlog</th><th>YTD</th>
        <th>FY backlog</th></tr></thead>
      <tbody>{rows}</tbody>
      <tfoot><tr class="hl"><td>Group</td>
        <td class="nm">{n} territories</td>
        <td class="n">{lakh(t['month_target'])}</td>
        <td class="n">{lakh(t['mtd_primary'])}</td>
        <td class="n">{pct(t['month_pct'])}</td>
        <td class="n">{signed(t['growth_pct'])}</td>
        <td class="n">{lakh(t['month_backlog'])}</td>
        <td class="n">{pct(t['ytd_pct'])}</td>
        <td class="n">{lakh(t['fy_backlog'])}</td></tr></tfoot>
    </table></div></div>
    <p class="note"><b>FY ACH % is not shown for each territory.</b> It is low
      for everyone early in the year, so it compares nobody with anybody. The FY
      backlog column tells you the same thing, and you can act on it.</p>
  </section>

  <section>
    <div class="shead"><h2>What these words mean</h2>
      <span>A quick guide</span></div>
    <dl class="gloss">
      <dt>AOP</dt><dd>The target for the period, set at the start of the year.</dd>
      <dt>Primary sales</dt><dd>Sold by the company to the distributor.
        Achievement is measured on this.</dd>
      <dt>LMTD</dt><dd>Last month up to the same day, so the comparison is fair.</dd>
      <dt>Backlog</dt><dd>Target minus what has been billed. A minus figure means
        ahead of plan.</dd>
      <dt>Group</dt><dd>Your territories added together, not the channel total
        from the sheet.</dd>
    </dl>
  </section>

  <footer>
    Made from the {e(month)} review sheet. Percentages and backlogs are worked
    out again from the AOP and sales columns. Achievement is measured on primary
    sales. All figures in &#8377; lakhs.
  </footer>
</div>
</body></html>"""



# -- the covering email ---------------------------------------------------
#
# Short, plain, and it does not repeat the report. Somebody opening this at
# half past eight wants to know whether they need to open the attachment
# today, and three figures answer that.
EMAIL_SUBJECT = '{region} - {month} position ({pct} of AOP)'

EMAIL_BODY = """Hello {first_name},

Your {month} position, attached.

  Month to date         {mtd} lakh against a plan of {target} ({pct})
  Against last month    {growth}
  Still owed this month {backlog} lakh

{line}

The attachment has the year-to-date and full-year position, where you sit
against the other heads, and what the remaining months need to run at.

Figures are from the Region Summary sheet as circulated{stamp}.
"""

TEAM_SUBJECT = '{name} - {month} position ({pct} of AOP)'

TEAM_BODY = """Hello {first_name},

Your group's {month} position, attached, with each territory's own report.

  Month to date              {mtd} lakh against a plan of {target} ({pct})
  Against last month         {growth}
  At or past plan            {ahead} of {total} territories

{line}

Figures are from the Region Summary sheet as circulated{stamp}.
"""


def email_for(d):
    """-> (subject, body) for one head's covering email."""
    h, y = d['head'], d['year']
    first = (h['head_name'] or '').split()[0].title() if h['head_name'] else 'there'
    p = h['month_pct']

    # The one line that changes. A head past plan being told what the year
    # still needs reads as the system refusing to acknowledge a good month.
    if p is None:
        line = ('There is no AOP against this territory this month, so there '
                'is no achievement figure.')
    elif p >= 100:
        line = 'You are past plan for the month.'
    elif y.get('required_monthly'):
        line = (f"The year needs {lakh(y['required_monthly'])} lakh a month "
                f"across the {y['months_remaining']} months left.")
    else:
        line = ''

    stamp = (f" on {d['snapshot']['as_of_date']}"
             if d['snapshot'].get('as_of_date') else '')
    ctx = {'region': h['region'], 'first_name': first,
           'month': d['snapshot'].get('as_of_month_label') or 'this month',
           'mtd': lakh(h['mtd_primary']), 'target': lakh(h['month_target']),
           'pct': pct(p), 'growth': signed(h['growth_pct']),
           'backlog': lakh(h['month_backlog']), 'line': line, 'stamp': stamp}
    return EMAIL_SUBJECT.format(**ctx), EMAIL_BODY.format(**ctx)


def team_email_for(d):
    """-> (subject, body) for a manager's covering email."""
    t = d['totals']
    heads = d['heads']
    ahead = sum(1 for h in heads if (h['month_pct'] or 0) >= 100)
    behind = [h['region'] for h in heads
              if h['month_pct'] is not None and h['month_pct'] < 70]
    line = (f"Needing attention: {', '.join(behind)}." if behind
            else 'No territory is below 70% of plan.')
    stamp = (f" on {d['snapshot']['as_of_date']}"
             if d['snapshot'].get('as_of_date') else '')
    ctx = {'name': d.get('name') or 'Your group', 'first_name': 'there',
           'month': d['snapshot'].get('as_of_month_label') or 'this month',
           'mtd': lakh(t['mtd_primary']), 'target': lakh(t['month_target']),
           'pct': pct(t['month_pct']), 'growth': signed(t['growth_pct']),
           'ahead': ahead, 'total': len(heads), 'line': line, 'stamp': stamp}
    return TEAM_SUBJECT.format(**ctx), TEAM_BODY.format(**ctx)
