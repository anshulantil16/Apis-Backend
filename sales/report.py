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
from html import escape

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
    return '--' if v is None else f'{v:.{dp}f}%'


def signed(v):
    if v is None:
        return '--'
    return ('+' if v > 0 else '') + f'{v:.0f}%'


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
        f'<text x="196" y="{y + 13}" text-anchor="end" class="lab"{cls}>{e(label)}</text>'
        f'<rect x="204" y="{y}" width="{max(width, 0):.1f}" height="18" rx="3" fill="{colour}"/>'
        f'<text x="{204 + max(width, 0) + 9:.1f}" y="{y + 13}" class="val">{e(value)}</text>'
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

.band{background:var(--card); border-bottom:1px solid var(--line);
      padding-block:30px 26px; margin-bottom:34px}
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
  body{background:#fff; font-size:11pt}
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
        lead = (f"<b>{month} is past plan</b> &mdash; {pct(h['month_pct'])} of the "
                f"AOP, with {lakh(-h['month_backlog'])} lakh more billed than asked for.")
    elif g_rank == 1:
        lead = (f"<b>The fastest-growing territory in {chan} this month</b> &mdash; "
                f"up {signed(h['growth_pct'])} on last month, where no other head "
                f"is close.")
    elif h['growth_pct'] is not None and h['growth_pct'] > 0:
        lead = (f"<b>{month} is ahead of last month</b> &mdash; "
                f"{signed(h['growth_pct'])}, at {pct(h['month_pct'])} of the AOP.")
    else:
        lead = (f"<b>{month} is behind both plan and last month</b> &mdash; "
                f"{pct(h['month_pct'])} of the AOP and {signed(h['growth_pct'])} "
                f"against August.")

    second = []
    if y.get('prior_pct') is not None and y['prior_months']:
        second.append(f"The months before this one ran at {pct(y['prior_pct'])} of "
                      f"plan, so the year to date is still carrying that start")
    if h['month_backlog'] > 0:
        second.append(f"{lakh(h['month_backlog'])} lakh is still owed with the "
                      f"month open")
    if y.get('required_monthly') and y.get('months_remaining'):
        second.append(f"and the full year needs {lakh(y['required_monthly'])} lakh "
                      f"a month across the {y['months_remaining']} months left, "
                      f"against a best month so far of {lakh(h['mtd_primary'])}")
    second_html = ('; '.join(second) + '.') if second else ''

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
              f"<b>This month, against this month's AOP.</b> Performance. "
              f"{lakh(h['month_backlog'])} lakh still owed, and the month is not finished.")
        + gauge('Year to date', h['ytd_pct'], h['ytd_actual'], h['ytd_target'],
                TONE[rag(h['ytd_pct'])],
                f"<b>April to now, against the plan for those months.</b> Also "
                f"performance, over {y['months_elapsed']} months instead of one."
                + (f" {e(chan)} as a whole is at {pct(ct.get('ytd_pct'))}."
                   if ct.get('ytd_pct') is not None else ''))
        + gauge('Full year', h['fy_pct'], h['fy_actual'], h['fy_target'], '#2a78d6',
                "<b>Progress, not a score.</b> FY ACH is the same figure as YTD ACH "
                "&mdash; nothing is sold past today &mdash; so this reads low by "
                "construction until March. It is the number on the sheet most often "
                "mistaken for a result.")
    )

    # -- the month chart --------------------------------------------------
    # One scale for every bar, chosen so the largest fits with room for its
    # label. Nothing here is drawn to its own scale.
    vals = [y.get('prior_monthly_avg') or 0, h['lmtd'], h['month_target'],
            h['mtd_primary'], y.get('required_monthly') or 0]
    top = max(vals) * 1.15 or 1
    W = 400.0

    def w(v):
        return max(float(v or 0), 0) / top * W

    ticks = ''
    for i in range(5):
        tv = top * i / 4
        x = 204 + W * i / 4
        ticks += (f'<line x1="{x:.1f}" y1="14" x2="{x:.1f}" y2="186" stroke="#EEF2F7"/>'
                  f'<text x="{x:.1f}" y="202" text-anchor="middle" class="axis">'
                  f'{lakh(tv, 0)}</text>')

    month_rows = ''
    if y.get('prior_monthly_avg'):
        month_rows += bar_row(20, f"Average of the first {y['prior_months']} months",
                              w(y['prior_monthly_avg']), '#A8DCC6',
                              lakh(y['prior_monthly_avg']), muted=True)
    month_rows += bar_row(52, 'Last month, same point', w(h['lmtd']), '#6FC9A3',
                          lakh(h['lmtd']), muted=True)

    # this month: billed, then a 2px gap, then what is still owed
    bw, ow = w(h['mtd_primary']), w(max(h['month_backlog'], 0))
    month_rows += (
        f'<g class="me">'
        f'<text x="196" y="97" text-anchor="end" class="lab">{e(month)} so far</text>'
        f'<rect x="204" y="84" width="{bw:.1f}" height="20" rx="3" fill="var(--done)"/>'
        + (f'<rect x="{204 + bw + 2:.1f}" y="84" width="{max(ow - 2, 0):.1f}" height="20" '
           f'rx="3" fill="var(--owed)"/>' if ow > 2 else '')
        + f'<text x="{204 + bw + ow + 9:.1f}" y="98" class="val">{lakh(h["mtd_primary"])}'
          + (f' &#43; {lakh(h["month_backlog"])} owed' if h['month_backlog'] > 0 else '')
        + f'</text></g>'
        f'<line x1="{204 + w(h["month_target"]):.1f}" y1="78" '
        f'x2="{204 + w(h["month_target"]):.1f}" y2="118" stroke="var(--plan)" stroke-width="2"/>'
        f'<text x="{204 + w(h["month_target"]):.1f}" y="132" text-anchor="middle" '
        f'class="val" fill="var(--plan)">AOP {lakh(h["month_target"])}</text>'
    )
    if y.get('required_monthly'):
        rw = w(y['required_monthly'])
        month_rows += (
            f'<text x="196" y="167" text-anchor="end" class="lab">Needed each month '
            f'from here</text>'
            f'<rect x="204" y="154" width="{rw:.1f}" height="18" rx="3" fill="#FDF0EA" '
            f'stroke="var(--owed)" stroke-width="1.5" stroke-dasharray="5 4"/>'
            f'<text x="{204 + rw + 9:.1f}" y="167" class="val" fill="var(--owed)">'
            f'{lakh(y["required_monthly"])}</text>')

    # -- peer board -------------------------------------------------------
    rows_svg, Y = '', 10
    PW = 330.0
    pmax = max([p['month_pct'] for p in peers] + [100]) * 1.1
    for p in peers:
        mine = p['id'] == h['id']
        bw2 = p['month_pct'] / pmax * PW
        if mine:
            rows_svg += (
                f'<g class="me"><rect x="244" y="{Y - 3}" width="446" height="24" fill="#F3F8FE"/>'
                f'<text x="258" y="{Y + 13}" text-anchor="end" class="lab">'
                f'{e(p["region"])} &#183; {e(p["head_name"])}</text>'
                f'<rect x="266" y="{Y}" width="{bw2:.1f}" height="18" rx="3" fill="var(--done)"/>'
                f'<text x="{266 + bw2 + 9:.1f}" y="{Y + 13}" class="val">'
                f'{pct(p["month_pct"])}</text></g>')
        else:
            rows_svg += (
                f'<text x="258" y="{Y + 13}" text-anchor="end" class="lab">'
                f'{e(p["region"])} &#183; {e(p["head_name"])}</text>'
                f'<rect x="266" y="{Y}" width="{bw2:.1f}" height="18" rx="3" '
                f'fill="var(--peer)" opacity=".55"/>'
                f'<text x="{266 + bw2 + 9:.1f}" y="{Y + 13}" class="val" '
                f'fill="#4A5568">{pct(p["month_pct"])}</text>')
        Y += 26
    plan_x = 266 + 100 / pmax * PW
    peer_h = Y + 34

    # -- the year ahead ---------------------------------------------------
    year_pts = []
    if y.get('plan_due_by_now_pct') is not None:
        year_pts.append(f"<b>{pct(y['plan_due_by_now_pct'])} of the annual plan was due "
                        f"by now</b> &mdash; {lakh(h['ytd_target'])} of "
                        f"{lakh(h['fy_target'])} lakh.")
    if h['fy_pct'] is not None:
        year_pts.append(f"<b>{pct(h['fy_pct'])} has been banked</b> &mdash; "
                        f"{lakh(h['fy_actual'])} lakh.")
    if y.get('plan_ahead') is not None and y.get('months_remaining'):
        year_pts.append(
            f"<b>The {lakh(h['fy_backlog'])} lakh of annual backlog is two different "
            f"things.</b> {lakh(y['plan_ahead'])} is the plan the next "
            f"{y['months_remaining']} months were always going to carry; "
            f"{lakh(y['catch_up'])} is catching up on months already closed.")
        year_pts.append(
            f"<b>{lakh(y['required_base'])} lakh a month just to stay on plan</b>, "
            f"and <b>{lakh(y['required_monthly'])} to also recover the shortfall</b>.")
    if y.get('required_vs_current'):
        year_pts.append(f"That is {y['required_vs_current']}&#215; the best month billed "
                        f"so far, and {y['required_vs_month_target']}&#215; this "
                        f"month's own AOP.")

    # Plan-against-achieved, three horizons, each filled left to right.
    def split(done, target, label, yy):
        t = float(target or 0)
        if t <= 0:
            return (f'<text x="0" y="{yy}" class="lab">{e(label)} &mdash; no plan</text>'
                    f'<rect x="0" y="{yy + 8}" width="420" height="18" rx="3" fill="#F0F3F7"/>')
        dw = min(max(float(done) / t, 0), 1) * 420
        out = (f'<text x="0" y="{yy}" class="lab">{e(label)} &mdash; plan '
               f'{lakh(target)}</text>'
               f'<rect x="0" y="{yy + 8}" width="{dw:.1f}" height="18" rx="3" fill="var(--done)"/>')
        if dw < 418:
            out += (f'<rect x="{dw + 2:.1f}" y="{yy + 8}" width="{418 - dw:.1f}" '
                    f'height="18" rx="3" fill="var(--owed)"/>')
        out += f'<text x="8" y="{yy + 21}" class="val" fill="#06281B">{lakh(done)}</text>'
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
    <div class="shead"><h2>The {prod['sfo_count']} officers</h2>
      <span>What the territory is doing per head</span></div>
    <div class="grid g3">
      <div class="tile"><div class="k">Per officer, this month</div>
        <div class="v {pm_tone}">{lakh(prod['per_sfo_mtd'])}</div>
        <div class="s">Against <b>{lakh(prod['channel_per_sfo_mtd'])}</b> across
          {e(chan)}. <b class="{pm_tone}">{pm}</b> the channel average.</div></div>
      <div class="tile"><div class="k">Per officer, April to date</div>
        <div class="v {py_tone}">{lakh(prod['per_sfo_ytd'])}</div>
        <div class="s">Against <b>{lakh(prod['channel_per_sfo_ytd'])}</b> across
          {e(chan)}. <b class="{py_tone}">{py}</b>.</div></div>
      <div class="tile"><div class="k">Share of {e(chan)}</div>
        <div class="v">{pct(sh.get('of_channel_mtd'), 1)}</div>
        <div class="s">This month, against <b>{pct(sh.get('of_channel_ytd'), 1)}</b>
          of its year to date.</div></div>
    </div>
    <p class="note"><b>Read the first two together.</b> Per-officer output this month
      and per-officer output for the year are the same story told twice &mdash; where
      they disagree, the month has changed and the year to date has not caught up
      with it yet.</p>
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
    <div class="shead"><h2>Three horizons</h2>
      <span>The sheet prints all three. They answer different questions.</span></div>
    <div class="grid g3">{gauges}</div>
  </section>

  <section>
    <div class="shead"><h2>{e(month)}</h2>
      <span>Against last month, and against plan</span></div>
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
        <span><i class="sw" style="background:var(--owed)"></i>Still owed</span>
        <span><i class="sw" style="background:var(--plan)"></i>AOP</span>
        <span><i class="sw ring"></i>Needed from here</span>
      </div>
      <div class="scroll"><svg viewBox="0 0 700 212" role="img"
        aria-label="Monthly billing against plan and against what the rest of the year needs">
        {ticks}
        <line x1="204" y1="14" x2="204" y2="186" stroke="#D5DCE6"/>
        {month_rows}
      </svg></div>
      <figcaption>Every bar on one scale, in lakhs. The dashed bar is not a
        forecast &mdash; it is the arithmetic of the annual plan divided by the
        months left to sell it in.</figcaption>
    </figure>
  </section>

  <section>
    <div class="shead"><h2>The year ahead</h2>
      <span>{y['months_elapsed']} months gone, {y['months_remaining']} to go</span></div>
    <div class="grid g2">
      <div>
        <ul class="pts">{''.join(f'<li>{p}</li>' for p in year_pts)}</ul>
        <p class="note"><b>Why this is separated out.</b> The sheet prints one annual
          backlog figure. Splitting it says which half is the problem: the months
          ahead carrying their own plan, or the months behind that did not.</p>
      </div>
      <figure>
        <div class="legend">
          <span><i class="sw" style="background:var(--done)"></i>Billed</span>
          <span><i class="sw" style="background:var(--owed)"></i>Backlog</span>
        </div>
        <svg viewBox="0 0 420 200" role="img"
             aria-label="Plan against achievement across the three horizons">
          {horizons}
        </svg>
        <figcaption>Each bar is its own plan, filled left to right, so the three
          horizons can be compared as proportions rather than as amounts.</figcaption>
      </figure>
    </div>
  </section>
{sfo_block}
  <section>
    <div class="shead"><h2>Against the other heads</h2>
      <span>{e(chan)} only &mdash; {len(peers)} heads</span></div>
    <figure>
      <div class="scroll"><svg viewBox="0 0 700 {peer_h}" role="img"
        aria-label="Achievement against AOP this month, by head">
        <line x1="266" y1="4" x2="266" y2="{Y - 4}" stroke="#D5DCE6"/>
        <line x1="{plan_x:.1f}" y1="4" x2="{plan_x:.1f}" y2="{Y - 4}"
              stroke="var(--plan)" stroke-width="1.5"/>
        <text x="{plan_x:.1f}" y="{Y + 16}" text-anchor="middle" class="axis"
              fill="var(--plan)">100% &mdash; plan</text>
        {rows_svg}
      </svg></div>
      <figcaption>{ordinal(m_rank)} of {of} on the month{
        f", {ordinal((rank.get('ytd_pct') or {}).get('position'))} of {of} on the year to date" if rank.get('ytd_pct') else ''}{
        f", and <b>{ordinal(g_rank)} of {of} on growth</b>" if g_rank else ''}.
        Ranked within {e(chan)} only: measured against a channel with a different
        field force and a differently phased plan, the comparison would mean
        nothing.</figcaption>
    </figure>
  </section>

  <section>
    <div class="shead"><h2>The line as it reads</h2>
      <span>Exactly as circulated, in &#8377; lakhs</span></div>
    <div class="tbl"><table>
      <thead><tr><th>Horizon</th><th>AOP</th><th>Achieved</th><th>ACH %</th>
        <th>Backlog</th></tr></thead>
      <tbody>
        <tr class="hl"><td>{e(month)}, month to date</td><td class="n">{lakh(h['month_target'])}</td>
          <td class="n">{lakh(h['mtd_primary'])}</td>
          <td class="n {rag(h['month_pct'])}">{pct(h['month_pct'])}</td>
          <td class="n">{lakh(h['month_backlog'])}</td></tr>
        <tr><td>April to date</td><td class="n">{lakh(h['ytd_target'])}</td>
          <td class="n">{lakh(h['ytd_actual'])}</td>
          <td class="n {rag(h['ytd_pct'])}">{pct(h['ytd_pct'])}</td>
          <td class="n">{lakh(h['ytd_backlog'])}</td></tr>
        <tr><td>Full financial year</td><td class="n">{lakh(h['fy_target'])}</td>
          <td class="n">{lakh(h['fy_actual'])}</td>
          <td class="n none">{pct(h['fy_pct'])}</td>
          <td class="n">{lakh(h['fy_backlog'])}</td></tr>
      </tbody>
    </table></div>
    <p class="note"><b>Supporting figures.</b>
      {f"Officers {prod['sfo_count']} &#183; " if prod.get('sfo_count') else ''}
      LMTD {lakh(h['lmtd'])} &#183; yesterday&#8217;s billing
      {lakh(flow['yesterday_billing'])} &#183; MTD secondary sales
      {lakh(h['mtd_secondary'])} &#183; growth over last month {signed(h['growth_pct'])}.</p>
  </section>

  <section>
    <div class="shead"><h2>What the terms mean</h2>
      <span>So nothing here has to be taken on trust</span></div>
    <dl class="gloss">
      <dt>AOP</dt><dd>The target for the period, as planned at the start of the year.</dd>
      <dt>Primary sales</dt><dd>Billed from the company to the distributor. Achievement
        is measured on this.</dd>
      <dt>Secondary sales</dt><dd>Sold on from the distributor to the retailer. Shown
        beside primary, not scored &mdash; below primary means stock is building at the
        distributor, above it means the trade is pulling down stock loaded earlier.
        Neither is good or bad on its own.</dd>
      <dt>LMTD</dt><dd>Last month to the same day, so the growth figure compares like
        with like rather than a part month against a whole one.</dd>
      <dt>MTD / YTD</dt><dd>Month to date, and April to date. The year opens in April.</dd>
      <dt>Backlog</dt><dd>AOP minus what has been billed. Negative means ahead of plan.</dd>
      <dt>FY ACH %</dt><dd>How much of the <i>annual</i> plan is banked so far. It is
        not a score &mdash; it rises through the year by construction, and reads low in
        September for everyone.</dd>
    </dl>
  </section>

  <footer>
    Built from the review sheet for {e(month)}, as circulated{
      f" and uploaded on {e(snap['created_at'][:10])}" if snap.get('created_at') else ''}.
    Every percentage and backlog here is recomputed from the AOP and sales columns
    rather than copied across, so these figures agree with the sheet by arithmetic
    rather than by transcription. Achievement is measured on primary sales. All
    figures in &#8377; lakhs.
  </footer>
</div>
</body></html>'''
