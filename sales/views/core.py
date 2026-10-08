"""SalesIQ core endpoints — upload, template, KPIs, breakdowns, trend,
forecast, insights, uploads management and Excel export."""
import io
from datetime import date, timedelta

import openpyxl
from django.db.models import Count, Max, Min, Q, Sum
from django.http import HttpResponse
from rest_framework.views import APIView
from .auth import SalesIQAdminView, SalesIQView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser

from ..models import (SalesUpload, SalesRecord, sync_actual_source, sheet_months,
                      elected_sources, ReviewSnapshot, ReviewRow)
from ..ingest import (map_headers, parse_date, parse_num, build_template,
                      NAME_FIELDS, normalise_name, clean_cell,
                      is_return_remark, is_not_a_sale, is_vacant,
                      TEXT_FIELDS, NUM_FIELDS, TEXT_MAX, DATE_FIELDS,
                      parse_bool, is_return_type, partition_unknown,
                      state_from_code, state_from_subregion, find_header_row)
from .. import aop as AOP
from .. import review as REVIEW

from ..forecasting import forecast_series, forecast_from_plan
from .. import status as STATUS
from .filters import (DIMENSIONS, FILTERABLE, _multi, apply_filters, detail_qs,
                      qs_for_dimension, money_base, sheet_fields,
                      apply_dim_filters, _period_bounds, _money, _pct_change,
                      NOT_SALES_ZONES, _not_sales_zone_q, in_plan_scope,
                      FINISHED_GOODS_PREFIX, display_floor,
                      filters_the_dump_cannot_answer, why_empty,
                      with_actuals, comparable_window,
                      same_months_last_year)

class SalesTemplateView(SalesIQView):
    def get(self, request):
        buf = build_template()
        resp = HttpResponse(
            buf.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = 'attachment; filename="SalesIQ_Template.xlsx"'
        return resp


def _reconcile_against_sheet(upload, watermark, stated, scale):
    """Does what we loaded add up to what the sheet says it should?

    The review sheet carries its own totals. Comparing them against the
    months this importer actually read turns every upload into a test of
    itself: if a month column were misread, dropped or counted twice, these
    would stop agreeing and say so, instead of a wrong number reaching the
    dashboard quietly.

    Rounded to the rupee before comparing. The sheet is written in lakhs to
    six decimal places, so a few paise of floating-point drift across twenty
    thousand rows is arithmetic, not a fault.
    """
    from ..analytics import financial_year

    rows = (upload.records.filter(id__gt=watermark)
            .values('period')
            .annotate(plan=Sum('target_amount'), actual=Sum('measured_amount'))
            .order_by('period'))
    by_month = {r['period']: (float(r['plan'] or 0), float(r['actual'] or 0))
                for r in rows if r['period']}
    if not by_month:
        return []

    planned = {m for m, (p, _) in by_month.items() if p > 0}
    if not planned:
        return []
    this_fy = financial_year(max(planned))
    # "To date" is every month of this financial year that has a result in
    # it -- which is exactly what the sheet means by YTD.
    ytd = sorted(m for m in planned
                 if financial_year(m) == this_fy and by_month[m][1] != 0)
    last_fy_same = [m for m in by_month
                    if financial_year(m) == this_fy - 1 and ytd
                    and m.month in {x.month for x in ytd}]

    loaded = {
        'fy_plan': sum(by_month[m][0] for m in by_month
                       if financial_year(m) == this_fy),
        'fy_actual': sum(by_month[m][1] for m in by_month
                         if financial_year(m) == this_fy),
        'ytd_plan': sum(by_month[m][0] for m in ytd),
        'ytd_actual': sum(by_month[m][1] for m in ytd),
        'last_ytd': sum(by_month[m][1] for m in last_fy_same),
    }
    LABEL = {'fy_plan': "the year's plan", 'fy_actual': 'achieved this year',
             'ytd_plan': 'plan to date', 'ytd_actual': 'achieved to date',
             'last_ytd': 'the same months last year'}

    agreed, differed = [], []
    for key, label in LABEL.items():
        if key not in stated:
            continue
        want = round(stated[key] * scale, 0)
        got = round(loaded.get(key, 0.0), 0)
        if abs(want - got) <= max(1.0, abs(want) * 0.0001):
            agreed.append(label)
        else:
            differed.append(f'{label}: your sheet says Rs {want:,.0f}, the months '
                            f'loaded come to Rs {got:,.0f}')

    out = []
    if agreed:
        out.append(
            'Checked against your own totals on this sheet (YTD AOP, YTD ACH, '
            'LYTD ACH and the FY columns): ' + ', '.join(agreed) +
            ' all agree to the rupee. Those columns are not loaded as data — '
            'each is its own months added up — but they make a good test that '
            'the months were read correctly.')
    if differed:
        out.append('Does not match your own totals on this sheet — '
                   + '; '.join(differed) +
                   '. Worth checking before trusting the figures.')
    if 'secondary' in stated and stated['secondary']:
        out.append(
            f'MTD SEC SALES on this sheet totals Rs {stated["secondary"] * scale:,.0f} '
            f'— secondary sales, month to date. It is read for this check but not '
            f'loaded as sales: it measures a different thing from the primary '
            f'figures beside it and adding the two would double the month.')
    return out


def _ingest_review(request, upload, ws, header_row, header_row_index=1):
    """Load the daily GTR-head review sheet into its own snapshot tables.

    Not into SalesRecord. Every money column here is a sum of transactions
    already held -- MTD contains today's invoices, YTD contains MTD, FY
    contains YTD -- so loading it as sales would report the same rupee three
    times and inflate every headline on the dashboard the moment somebody
    uploaded the morning review.
    """
    cols, checks, as_of_month, unknown = REVIEW.map_columns(header_row)

    missing = [f for f in ('head_name', 'mtd_primary', 'month_target') if f not in cols]
    if missing:
        return Response({'error': 'This looks like the daily review sheet but is '
                                  'missing ' + ', '.join(missing).replace('_', ' ') +
                                  '. Expected columns like "GTR HEAD", '
                                  '"MTD Sep-26 PRI SALES" and "Sep-26 AOP".'},
                        status=400)

    # The day the sheet describes is not written on it anywhere -- only the
    # month is, in the column headers. It can be supplied with the upload;
    # otherwise today, which is right for a sheet circulated this morning and
    # wrong for one uploaded late, so it is stored where it can be corrected.
    as_of_date = parse_date(request.query_params.get('as_of')) or date.today()

    snapshot = ReviewSnapshot.objects.create(
        upload=upload,
        filename=(upload.filename or '')[:255],
        sheet_name=(ws.title or '')[:120],
        uploaded_by=str(request.query_params.get('user') or '')[:200],
        as_of_month=as_of_month,
        as_of_date=as_of_date,
    )

    # Same reasoning as the AOP sheet: this file is written in lakhs and the
    # invoice dump beside it in rupees, so the unit is settled before a single
    # row is stored. Nothing is written until it is known -- rows are held
    # here and scaled on the way out.
    pending, sample = [], []
    stated = {}
    channel = region = ''
    skipped = 0

    for row_no, row in enumerate(
            ws.iter_rows(min_row=header_row_index + 1, values_only=True),
            start=header_row_index + 1):
        if not row or not any(v is not None and str(v).strip() != '' for v in row):
            continue

        vals = REVIEW.read_row(row, cols)

        # Merged cells read as blank below the first row of the merge, so a
        # split territory's second line arrives with no region at all.
        channel = REVIEW.carry_forward(vals.get('channel'), channel)
        region = REVIEW.carry_forward(vals.get('region'), region)

        total_row = REVIEW.is_total_row(row, cols)
        if total_row:
            # The sheet's own addition, kept to check ours against.
            for name, ci in checks.items():
                if ci < len(row):
                    stated.setdefault(name, parse_num(row[ci], 0.0))
            for f in REVIEW.MONEY_FIELDS:
                stated.setdefault('total_' + f, 0.0)
            label = (vals.get('channel') or vals.get('region')
                     or vals.get('head_name') or '')
            if 'grand' in label.lower():
                for f in REVIEW.MONEY_FIELDS:
                    stated['total_' + f] = vals.get(f, 0.0)

        name = (vals.get('head_name') or '').strip()
        money = [vals.get(f, 0.0) for f in REVIEW.MONEY_FIELDS]
        if not total_row and not name and not any(money):
            skipped += 1
            continue

        sample.extend(abs(v) for v in money if v)
        pending.append((row_no, total_row, channel, region, name, vals))

    if not pending:
        snapshot.delete()
        return Response({'error': 'No rows found under the header on this sheet.'},
                        status=400)

    scale, unit = AOP.detect_money_scale(sample)
    snapshot.source_unit = unit

    # The head's identity, from the AOP sheet's own ID columns. A name is not
    # an identity -- it is spelled several ways across a year of exports --
    # and a report addressed to a spelling goes to nobody.
    codes = {}
    for nm, code in (SalesRecord.objects
                     .filter(source=SalesRecord.SOURCE_PLAN)
                     .exclude(rsm_code='').exclude(rsm='')
                     .values_list('rsm', 'rsm_code').distinct()):
        codes.setdefault(normalise_name(nm).lower(), code)

    rows = []
    for row_no, total_row, ch, rg, name, vals in pending:
        rows.append(ReviewRow(
            snapshot=snapshot,
            channel=(ch or '')[:60],
            region=(rg or '')[:60],
            head_name=normalise_name(name)[:200],
            head_code=codes.get(normalise_name(name).lower(), '')[:60],
            sfo_count=vals.get('sfo_count', 0),
            is_total=total_row,
            row_no=row_no,
            **{f: round(vals.get(f, 0.0) * scale, 2) for f in REVIEW.MONEY_FIELDS}
        ))
    ReviewRow.objects.bulk_create(rows, batch_size=500)

    heads = [r for r in rows if not r.is_total]
    snapshot.row_count = len(heads)

    warnings, notes = [], []
    if unknown:
        warnings.append('Columns not recognised and not loaded: ' + ', '.join(unknown[:8]))
    if as_of_month is None:
        warnings.append('No month could be read from the column headers, so this '
                        'snapshot is not filed under a month. Expected a column '
                        'headed like "Sep-26 AOP".')

    # Our addition against the sheet's own Grand Total. A mismatch is reported
    # rather than resolved: the sheet is what the business circulated, and an
    # importer that quietly prefers its own sum is an importer nobody can
    # check.
    for field in ('mtd_primary', 'ytd_actual', 'fy_target'):
        claimed = stated.get('total_' + field)
        if not claimed:
            continue
        ours = sum(float(getattr(r, field)) for r in heads)
        claimed = claimed * scale
        if claimed and abs(ours - claimed) > max(abs(claimed) * 0.005, 1.0):
            warnings.append(
                f'{field.replace("_", " ")}: the head rows add up to '
                f'{ours:,.0f} but the sheet\'s Grand Total says {claimed:,.0f}.')

    notes.append(f'{len(heads)} heads read from "{ws.title}"'
                 + (f', {as_of_month:%B %Y}' if as_of_month else '')
                 + f'; figures read as {unit}.')
    if skipped:
        notes.append(f'{skipped} blank rows skipped.')
    vacant = sum(1 for r in heads if not r.head_code)
    if vacant:
        notes.append(f'{vacant} of {len(heads)} heads could not be matched to an '
                     f'APIS ID on the AOP sheet, so their report cannot be '
                     f'addressed automatically yet.')

    snapshot.warnings, snapshot.notes = warnings, notes
    snapshot.save(update_fields=['row_count', 'source_unit', 'warnings', 'notes'])

    return Response({'rows': len(heads), 'skipped': skipped,
                     'warnings': warnings, 'notes': notes,
                     'review_snapshot': snapshot.id})


def _ingest_aop(request, upload, ws, header_row, header_row_index=1):
    """Load the AOP-vs-ACH sheet, unpivoting each row into one per month."""
    dims, months, unknown = AOP.map_columns(header_row)
    # The APIS / BIZOM ID columns beside each people column. Found by where
    # they sit, not by what they are headed -- see aop.map_person_codes.
    code_cols = AOP.map_person_codes(header_row, dims)

    if not months:
        return Response({'error': 'No month columns found. This sheet should carry '
                                  'columns like "Apr-26 AOP" and "Apr-26".'}, status=400)

    # One upload row per FILE, created by the caller. A sheet does not get
    # its own: a two-tab workbook was appearing in the list twice under the
    # same name, which reads as having uploaded it twice.
    watermark = SalesRecord.objects.aggregate(m=Max('id'))['m'] or 0

    batch, total_rev, total_target = [], 0.0, 0.0
    lo = hi = None
    empty_rows = 0
    row_no = header_row_index

    # This sheet is written in lakhs and the dump beside it in rupees, so the
    # unit has to be settled before a single row is stored -- otherwise one
    # row lands in one unit and its neighbour in another. It is read off the
    # first few hundred figures and then applied to everything, including the
    # rows already buffered when it was decided. Nothing is flushed to the
    # database until it is known.
    SAMPLE_TARGET = 400
    sample, scale, unit = [], None, 'rupees'

    # The sheet states its own totals in YTD AOP, YTD ACH, LYTD ACH and the
    # FY columns. They are not stored -- each is its own months added up --
    # but they are the business's own statement of what those months come to,
    # which makes them the best available test of whether this importer read
    # the months correctly. Totalled here, checked at the end.
    summary_cols = AOP.map_summary_columns(header_row)
    stated = {k: 0.0 for k in summary_cols}

    def _flush(recs, mult):
        nonlocal total_rev, total_target
        for r in recs:
            r.net_amount = round(float(r.net_amount) * mult, 2)
            r.measured_amount = r.net_amount
            r.target_amount = round(float(r.target_amount) * mult, 2)
            total_rev += float(r.net_amount)
            total_target += float(r.target_amount)
        SalesRecord.objects.bulk_create(recs, batch_size=1000)

    try:
        for row in ws.iter_rows(min_row=header_row_index + 1, values_only=True):
            row_no += 1
            if not any(v is not None and str(v).strip() != '' for v in row):
                continue
            entries = AOP.unpivot(row, dims, months)
            codes = AOP.read_person_codes(row, code_cols)
            if not entries:
                empty_rows += 1
                continue
            for k, v in AOP.read_summary(row, summary_cols).items():
                stated[k] += v
            if scale is None:
                for e in entries:
                    if e['net_amount']:
                        sample.append(e['net_amount'])
                    if e['target_amount']:
                        sample.append(e['target_amount'])
            for e in entries:
                period = e['period']
                rec = SalesRecord(
                    upload=upload,
                    source=SalesRecord.SOURCE_PLAN,
                    order_date=period,
                    period=period,
                    net_amount=e['net_amount'],
                    measured_amount=e['net_amount'],
                    target_amount=e['target_amount'],
                    sfo_count=e['sfo_count'],
                )
                for field in ('channel', 'sales_head', 'rsm', 'asm', 'zone', 'state',
                              'item_alt_code', 'product_name', 'brand'):
                    val = str(e.get(field) or '').strip()
                    if field in NAME_FIELDS:
                        val = normalise_name(val)
                    setattr(rec, field, val[:TEXT_MAX.get(field, 200)])
                # Sub-Region is a selling territory (AP-1), not a state. It
                # keeps its own column, and the state is read off its code
                # so this sheet and the invoice dump name states the same way.
                rec.subzone = rec.state[:100]
                rec.state = state_from_subregion(rec.subzone)
                # A row attribute, not a monthly one: the same person owns
                # every month the row unpivots into.
                for code_field, code in codes.items():
                    setattr(rec, code_field, code)
                batch.append(rec)
                lo = period if lo is None or period < lo else lo
                hi = period if hi is None or period > hi else hi

            if scale is None and len(sample) >= SAMPLE_TARGET:
                scale, unit = AOP.detect_money_scale(sample)
            if scale is not None and len(batch) >= 2000:
                _flush(batch, scale)
                batch = []
        # A sheet shorter than the sample target decides here instead.
        if scale is None:
            scale, unit = AOP.detect_money_scale(sample)
        if batch:
            _flush(batch, scale)
    except Exception as e:
        # Only this sheet's rows. The upload belongs to the file, and another
        # sheet may already have loaded cleanly into it.
        upload.records.filter(id__gt=watermark).delete()
        return Response({'error': f'Failed while reading row {row_no}: {e}'}, status=400)

    count = upload.records.filter(id__gt=watermark).count()
    if count == 0:
        return Response({'error': 'No usable rows — every row was empty across all months.'},
                        status=400)

    warnings, notes = [], []
    plan_months = sorted({m for _, m, t in months if t})
    notes.append(
        f'{count:,} month-rows built from this sheet — each row was spread across the '
        f'{len(set(m for _, m, _ in months))} month columns it carries.')
    if stated:
        notes.extend(_reconcile_against_sheet(upload, watermark, stated, scale))
    if scale != 1:
        notes.append(
            f'Figures on this sheet are in {unit} — a plan cell reading 4.57 is '
            f'Rs 4,57,000 — so every one was multiplied by {scale:,} to match the '
            f'invoice data, which is in rupees. Total plan loaded: '
            f'Rs {total_target:,.0f}.')
    if plan_months:
        notes.append(
            f'Plan (AOP) loaded for {len(plan_months)} months, '
            f'{plan_months[0]:%b %Y} to {plan_months[-1]:%b %Y}. '
            f'Targets and achievement now sit side by side on the dashboard.')
    skipped, unrecognised = [], []
    for h in unknown:
        (skipped if h in AOP.IGNORED else unrecognised).append(h)
    if skipped:
        notes.append(
            f'{len(skipped)} total column(s) skipped on purpose: {", ".join(skipped)}. '
            f'Each is its own monthly columns added up, and storing a total beside the '
            f'parts it is made of double-counts the year.')
    if unrecognised:
        warnings.append(
            f'{len(unrecognised)} column(s) were not recognised and are ignored: '
            f'{", ".join(unrecognised[:12])}.')
    if empty_rows:
        notes.append(f'{empty_rows} row(s) had no figure in any month and were skipped.')

    # Which file wins which month is decided once, after every sheet has been
    # read, so it is reported by the caller rather than guessed at here.


    return Response({
        'message': f'Imported {count:,} month-rows.',
        'upload_id': upload.id,
        'rows': count,
        'skipped': empty_rows,
        'total_revenue': _money(total_rev),
        'total_target': _money(total_target),
        'file_kind': 'aop',
        'period': {'from': lo.isoformat() if lo else None,
                   'to': hi.isoformat() if hi else None},
        'detected_columns': sorted(dims.keys()),
        'skipped_columns': skipped,
        'unrecognised_columns': unrecognised,
        'cancelled_rows': 0,
        'return_rows': 0,
        'warnings': warnings,
        'notes': notes,
    })


class SalesUploadView(SalesIQAdminView):
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        """Load every sheet in the workbook, routing each by its own headers.

        The two primary-sales files are normally kept as two tabs of one
        workbook. Reading only wb.active meant whichever tab happened to be
        selected when the file was last saved decided what got imported — the
        other one was dropped without a word, and if the selected tab was the
        AOP sheet the dump parser rejected it with a complaint about a missing
        date column that was really a complaint about the wrong sheet.
        """
        f = request.FILES.get('file')
        if not f:
            return Response({'error': 'No file provided.'}, status=400)
        try:
            wb = openpyxl.load_workbook(f, data_only=True, read_only=True)
        except Exception as e:
            return Response({'error': f'Cannot read file: {e}'}, status=400)

        # One row in the uploaded-files list per FILE. Each sheet used to
        # create its own, so a two-tab workbook appeared twice under the same
        # name — which reads as having uploaded it twice by mistake.
        upload = SalesUpload.objects.create(
            filename=(f.name or '')[:255],
            uploaded_by=str(request.query_params.get('user') or '')[:200],
            status='completed',
        )

        outcomes = []
        for ws in wb.worksheets:
            header_row, header_at = find_header_row(
                ws, recognises=lambda r: max(len(map_headers(r)[0]),
                                             len(AOP.map_columns(r)[0])
                                             + len(AOP.map_columns(r)[1]),
                                             len(REVIEW.map_columns(r)[0])
                                             + len(REVIEW.map_columns(r)[1])))
            if header_row is None:
                continue        # an empty tab is not an error
            if REVIEW.looks_like_review_sheet(header_row):
                resp = _ingest_review(request, upload, ws, header_row, header_at)
            elif AOP.looks_like_aop_sheet(header_row):
                resp = _ingest_aop(request, upload, ws, header_row, header_at)
            else:
                resp = _ingest_dump(request, upload, ws, header_row, header_at)
            outcomes.append((ws.title, resp))

        if not outcomes:
            upload.delete()
            return Response({'error': 'The workbook has no sheets with any data in them.'},
                            status=400)

        good = [(t, r) for t, r in outcomes if r.status_code == 200]
        if not good:
            # Nothing loaded, so nothing to show. An upload row left behind
            # here is the "0 rows - Rs 0" ghost that used to sit in the list
            # for good, because failing only ever set a status and deleted
            # the records underneath it.
            upload.delete()
            # Every sheet failed. One sheet is the common case, so answer with
            # its own error rather than a summary nobody can act on.
            title, resp = outcomes[0]
            if len(outcomes) > 1:
                resp.data['error'] = (
                    f'Nothing could be imported. "{title}": '
                    f'{resp.data.get("error", "unreadable")}')
                resp.data['sheets'] = {t: r.data.get('error') for t, r in outcomes}
            return resp

        # Counted back off the rows that actually landed, rather than carried
        # along as the sheets are read. A running total and the table it
        # describes drift the moment anything is skipped, and the list header
        # then disagrees with the row beneath it.
        sync_actual_source()
        agg = upload.records.aggregate(
            n=Count('id'), lo=Min('order_date'), hi=Max('order_date'))
        # Revenue is counted over what actually sold. A cancelled invoice is
        # stored and reported in the row count — it is a real document — but
        # it is not money, here any more than on the dashboard.
        # The same two exclusions the dashboard applies. The number printed
        # beside a file in the uploads list and the number on the dashboard
        # are the same claim, so they have to be counted the same way.
        earned = (in_plan_scope(upload.records.exclude(is_cancelled=True)
                                .exclude(is_not_sales=True))
                  .aggregate(rev=Sum('net_amount'))['rev'])
        upload.row_count = agg['n'] or 0
        upload.skipped_rows = sum(r.data.get('skipped', 0) for _, r in good)
        upload.total_revenue = round(float(earned or 0), 2)
        upload.period_start, upload.period_end = agg['lo'], agg['hi']
        upload.warnings = [f'{t}: {w}' for t, r in good for w in r.data.get('warnings', [])]
        upload.notes = [f'{t}: {n}' for t, r in good for n in r.data.get('notes', [])]

        # Where each month's sales actually came from. This is a fact about
        # the whole upload, not about either sheet on its own, and it is the
        # one thing somebody checking a figure against Excel most needs to
        # know -- the two files cover different spans and different channels,
        # so which one a month came from decides what the month means.
        elected = elected_sources()
        # Only worth saying when both files are in play. With one loaded
        # there is no election to explain, and claiming there is sends
        # somebody looking for a second file they never uploaded.
        if (elected
                and SalesRecord.objects.filter(
                    source=SalesRecord.SOURCE_INVOICE).exists()
                and SalesRecord.objects.filter(
                    source=SalesRecord.SOURCE_PLAN).exists()):
            def _span(ms):
                ms = sorted(ms)
                if not ms:
                    return ''
                if len(ms) == 1:
                    return ms[0].strftime('%b %Y')
                return f"{ms[0].strftime('%b %Y')} to {ms[-1].strftime('%b %Y')}"
            inv = [m for m, src in elected.items() if src == 'invoice']
            pln = [m for m, src in elected.items() if src == 'plan']
            # Attributed like every other message, but to both sheets: it is
            # a fact about the election BETWEEN them, and an unattributed
            # message leaves the reader guessing which half it is about.
            parts = ['Both sheets: sales by month come from two places, '
                     'never both at once.']
            if pln:
                parts.append(
                    f'{_span(pln)} ({len(pln)} month(s)) from the review sheet, '
                    f'which is the record your own YTD ACH column is read off.')
            if inv:
                parts.append(
                    f'{_span(inv)} ({len(inv)} month(s)) from the invoice dump, '
                    f'the only record that reaches those months.')
            parts.append(
                'The invoice dump is also what answers a date range, because it '
                'is the only one of the two that knows what day a sale happened '
                'on, and it is where product, SKU and customer breakdowns come '
                'from. Those cover its months only.')
            upload.notes.append(' '.join(parts))
        for title, resp in outcomes:
            if resp.status_code != 200:
                upload.warnings.insert(
                    0, f'{title}: not imported - {resp.data.get("error", "unreadable")}')
        upload.save()

        if len(good) == 1 and len(outcomes) == 1:
            single = good[0][1]
            single.data['rows'] = upload.row_count
            single.data['total_revenue'] = _money(upload.total_revenue)
            # The file-level notes -- which months came from which file above
            # all -- are added after the sheet has answered, so the sheet's own
            # response does not have them. A one-sheet upload was therefore the
            # one case where the operator never saw the election, which is
            # exactly the case where a second file has just changed it.
            single.data['notes'] = upload.notes
            single.data['warnings'] = upload.warnings
            return single

        # More than one sheet loaded: merge into one answer, and say which
        # sheet each figure came from.
        merged = {
            'message': f'Imported {upload.row_count:,} rows from {len(good)} sheet(s).',
            'rows': upload.row_count,
            'skipped': sum(r.data.get('skipped', 0) for _, r in good),
            'total_revenue': _money(upload.total_revenue),
            'cancelled_rows': sum(r.data.get('cancelled_rows', 0) for _, r in good),
            'return_rows': sum(r.data.get('return_rows', 0) for _, r in good),
            'sheets': {t: {'rows': r.data.get('rows', 0),
                           'kind': r.data.get('file_kind', 'dump')} for t, r in good},
            'detected_columns': sorted({c for _, r in good
                                        for c in r.data.get('detected_columns', [])}),
            'skipped_columns': sorted({c for _, r in good
                                       for c in r.data.get('skipped_columns', [])}),
            'unrecognised_columns': sorted({c for _, r in good
                                            for c in r.data.get('unrecognised_columns', [])}),
            'warnings': upload.warnings,
            'notes': upload.notes,
        }
        return Response(merged)


def _ingest_dump(request, upload, ws, header_row, header_row_index=1):
    """Load one Pre-Sales Dump sheet."""
    col_map, unknown = map_headers(header_row)
    not_sales_rows = 0
    # The Pre-Sales Dump's sales value is Taxable Amount, which is its own
    # column rather than a spelling of "Net Amount" — so it counts here.
    VALUE_COLUMNS = ('net_amount', 'gross_amount', 'taxable_amount')
    if not any(c in col_map for c in ('order_date', 'invoice_date', 'posting_date')):
        return Response({
            'error': 'No date column found. One column must be the order/invoice date.',
            'detected_columns': sorted(col_map.keys()),
            'unrecognised_columns': unknown,
        }, status=400)
    if not any(c in col_map for c in VALUE_COLUMNS):
        return Response({
            'error': 'No sales value column found. Add a "Net Amount" '
                     '(or "Amount" / "Sales Value") column.',
            'detected_columns': sorted(col_map.keys()),
            'unrecognised_columns': unknown,
        }, status=400)

    def cell(row, field):
        ci = col_map.get(field)
        if ci is None or ci >= len(row):
            return None
        return row[ci]

    # One upload row per FILE, created by the caller. A sheet does not get
    # its own: a two-tab workbook was appearing in the list twice under the
    # same name, which reads as having uploaded it twice.
    watermark = SalesRecord.objects.aggregate(m=Max('id'))['m'] or 0

    batch, total_rev = [], 0.0
    no_date = bad_value = 0
    cancelled_rows = return_rows = 0
    # What the Type column actually holds, and how much money sits on
    # lines that carry no product.
    line_types = {}
    non_item_value = 0.0
    return_negative = return_positive = 0
    lo = hi = None
    row_no = header_row_index
    try:
        for row in ws.iter_rows(min_row=header_row_index + 1, values_only=True):
            row_no += 1
            if not any(v is not None and str(v).strip() != '' for v in row):
                continue
            # Invoice Date and Posting Date now map to their own columns,
            # so they no longer double as aliases for Order Date. An export
            # that carries only one of them must still land in a month, so
            # they remain the fallback here, in that order.
            od = (parse_date(cell(row, 'order_date'))
                  or parse_date(cell(row, 'invoice_date'))
                  or parse_date(cell(row, 'posting_date')))
            if od is None:
                no_date += 1
                continue

            # ── is this row a sale at all? ────────────────────────
            # An ERP dump carries cancelled invoices and credit memos in
            # the same sheet as live sales. Both are kept — they are real
            # documents and the ledger has to reconcile — but a cancelled
            # row must never reach a sales figure.
            cancelled = parse_bool(cell(row, 'is_cancelled'))
            doc_type = cell(row, 'document_type')
            # V-REMARS is the only column in this export that says what a line
            # is. Without it 1,693 returns in the first real file came through
            # as ordinary sales that happened to be negative.
            remark = clean_cell(cell(row, 'remarks'))
            returned = is_return_type(doc_type) or is_return_remark(remark)
            not_sales = is_not_a_sale(remark)
            if cancelled:
                cancelled_rows += 1
            if returned:
                return_rows += 1
            if not_sales:
                not_sales_rows += 1
            lt = str(cell(row, 'line_type') or '').strip()
            if lt:
                line_types[lt] = line_types.get(lt, 0) + 1

            # ── money ─────────────────────────────────────────────────
            # Taxable Amount is the sales value the business reports on:
            # after scheme and discount, before GST. It wins over a
            # generic "amount" column when the dump carries both.
            taxable = parse_num(cell(row, 'taxable_amount'))
            gross = parse_num(cell(row, 'gross_amount'))
            net = parse_num(cell(row, 'net_amount'))

            inv_disc = parse_num(cell(row, 'invoice_discount'))
            retail = parse_num(cell(row, 'retail_scheme'))
            wholesale = parse_num(cell(row, 'wholesale_scheme'))
            igst = parse_num(cell(row, 'igst_amount'))
            cgst = parse_num(cell(row, 'cgst_amount'))
            sgst = parse_num(cell(row, 'sgst_amount'))

            if taxable:
                net = taxable
            # Discount and tax are summed from their parts when the dump
            # splits them, rather than asking for a pre-totalled column
            # that would then disagree with the parts beside it.
            discount = parse_num(cell(row, 'discount')) or (inv_disc + retail + wholesale)
            tax = parse_num(cell(row, 'tax')) or (igst + cgst + sgst)

            if not gross and net:
                gross = net + discount
            # Fall back to gross when the export has no explicit net column.
            if not net and gross:
                net = gross - discount
            # A free sample really is worth nothing, and the business has
            # already said so in V-REMARS. Warning "check the amount column"
            # about it sends somebody looking for an export fault that is not
            # there -- all four in the first real file were samples and
            # packaging given away, already excluded from sales.
            if not net and not gross and not cancelled and not not_sales:
                bad_value += 1

            if lt and lt.lower() != 'item':
                non_item_value += net
            if returned:
                if net < 0:
                    return_negative += 1
                elif net > 0:
                    return_positive += 1

            rec = SalesRecord(
                upload=upload,
                order_date=od,
                period=od.replace(day=1),
                quantity=parse_num(cell(row, 'quantity')),
                unit_price=parse_num(cell(row, 'unit_price')),
                gross_amount=gross,
                discount=discount,
                tax=tax,
                net_amount=net,
                # The row's own figure, never overwritten. sync_actual_source()
                # elects net_amount from this each time it runs.
                measured_amount=net,
                target_amount=parse_num(cell(row, 'target_amount')),
                is_cancelled=cancelled,
                is_return=returned,
                is_not_sales=not_sales,
            )
            for tf in TEXT_FIELDS:
                # clean_cell, not str().strip(): a broken VLOOKUP leaves #N/A
                # in the cell and it was being stored as an item code and a
                # district, then offered in the filter bar as a thing to
                # group the business by.
                s = clean_cell(cell(row, tf))
                if tf in NAME_FIELDS:
                    s = normalise_name(s)
                setattr(rec, tf, s[:TEXT_MAX.get(tf, 150)])
            # The dump names no state, only a GST code. Filled in only
            # when the sheet gave no state of its own, so a file that does
            # carry one keeps its own spelling.
            if not rec.state:
                rec.state = state_from_code(rec.customer_state_code)
            for nf in NUM_FIELDS:
                # The headline five are derived above; the rest are stored
                # verbatim so the sheet can be reconciled against the ERP.
                if nf in ('quantity', 'unit_price', 'gross_amount', 'discount',
                          'tax', 'net_amount', 'target_amount'):
                    continue
                setattr(rec, nf, parse_num(cell(row, nf)))
            for df in DATE_FIELDS:
                setattr(rec, df, parse_date(cell(row, df)))
            batch.append(rec)
            # A cancelled invoice is not revenue.
            if not cancelled:
                total_rev += net
            lo = od if lo is None or od < lo else lo
            hi = od if hi is None or od > hi else hi

            if len(batch) >= 2000:
                SalesRecord.objects.bulk_create(batch, batch_size=1000)
                batch = []
        if batch:
            SalesRecord.objects.bulk_create(batch, batch_size=1000)
    except Exception as e:
        # Only this sheet's rows. The upload belongs to the file, and another
        # sheet may already have loaded cleanly into it.
        upload.records.filter(id__gt=watermark).delete()
        return Response({'error': f'Failed while reading row {row_no}: {e}'}, status=400)

    count = upload.records.filter(id__gt=watermark).count()
    if count == 0:
        return Response({
            'error': 'No usable rows found — every row was missing a valid date.',
            'detected_columns': sorted(col_map.keys()),
            'unrecognised_columns': unknown,
        }, status=400)

    warnings, notes = [], []
    if line_types:
        notes.append(
            'Line types in the Type column: '
            + ', '.join(f'{k} ({v:,})' for k, v in
                        sorted(line_types.items(), key=lambda x: -x[1])[:8]) + '.')
    if non_item_value:
        notes.append(
            f'Rs {non_item_value:,.0f} of the total sits on lines with no product '
            f'on them - freight, rounding and other charges posted straight to a '
            f'ledger account. They count in the sales total, as they do on the '
            f'invoice, but they cannot appear in a product or SKU breakdown. That '
            f'is why those views add up to slightly less than the headline.')
    if cancelled_rows:
        notes.append(
            f'{cancelled_rows} cancelled row(s) were loaded but are excluded from '
            f'every sales figure. They stay in the data so the file still '
            f'reconciles against the ERP.')
    if return_rows:
        if return_positive and not return_negative:
            warnings.append(
                f'{return_rows} return row(s), every one carrying a POSITIVE amount - '
                f'so they are ADDING to sales instead of reducing them. Say the word '
                f'and we will flip the sign on load.')
        elif return_negative and not return_positive:
            notes.append(
                f'{return_rows} return row(s), all negative - they already reduce '
                f'sales, which is right. Nothing to change.')
        else:
            warnings.append(
                f'{return_rows} return row(s): {return_negative:,} negative and '
                f'{return_positive:,} positive. A mix means the export is not '
                f'consistent about which way a return points.')
    if no_date:
        warnings.append(f'{no_date} row(s) skipped — the date column was empty or '
                        f'unreadable. Those sales are NOT in the dashboard.')
    if bad_value:
        warnings.append(f'{bad_value} row(s) had no sales value (treated as 0). '
                        f'Check the amount column in your export.')
    if not_sales_rows:
        not_sales_value = float(
            upload.records.filter(id__gt=watermark, is_not_sales=True)
            .aggregate(v=Sum('measured_amount'))['v'] or 0)
        notes.append(
            f'{not_sales_rows:,} line(s) worth Rs {not_sales_value:,.0f} are marked '
            f'"NOT A PART OF SALES" in your V-REMARS column — freight, packaging and '
            f'spares billed on a sales invoice. They are loaded and kept, so the file '
            f'still reconciles with the ERP, but they are left out of every sales '
            f'figure because you have already ruled them out.')
    if return_rows:
        notes.append(
            f'{return_rows:,} line(s) are returns or credit notes (SR, GOOD SR, '
            f'SCHEME CN in V-REMARS). They reduce sales, as they should, and can now '
            f'be reported on separately.')
    skipped, unrecognised = partition_unknown(unknown)
    if unrecognised:
        shown = ', '.join(unrecognised[:12]) + (' …' if len(unrecognised) > 12 else '')
        warnings.append(f'{len(unrecognised)} column(s) were not recognised and are '
                        f'ignored: {shown}. Rename them to match the template, or ask '
                        f'for them to be added.')
    # 'state' is derived from the customer's state code when no state column
    # is present, so asking col_map alone produced a note telling the user
    # their state view would be empty while it was in fact showing all 24 of
    # their states.
    DERIVED_FROM = {'state': ('state', 'customer_state_code')}
    missing = [d for d in ('state', 'category', 'channel', 'salesperson')
               if not any(c in col_map for c in DERIVED_FROM.get(d, (d,)))]
    if missing:
        notes.append('No ' + ', '.join(missing) + ' column — those breakdown views '
                        'will be empty. Add the column and re-upload to enable them.')


    return Response({
        'message': f'Imported {count:,} rows.',
        'upload_id': upload.id,
        'rows': count,
        'skipped': no_date,
        'total_revenue': _money(total_rev),
        'period': {'from': lo.isoformat() if lo else None,
                   'to': hi.isoformat() if hi else None},
        'detected_columns': sorted(col_map.keys()),
        # Split so the screen can say "skipped on purpose" and "we don't
        # know what this is" differently. Lumping them together made every
        # upload of a normal ERP dump look like sixteen mapping failures.
        'skipped_columns': skipped,
        'unrecognised_columns': unrecognised,
        'cancelled_rows': cancelled_rows,
        'return_rows': return_rows,
        'line_types': line_types,
        'warnings': warnings,
        'notes': notes,
    })



class SalesOverviewView(SalesIQView):
    """Headline KPIs + comparison against the preceding equal-length window."""

    def get(self, request):
        # Two queries, because there are two files and they answer different
        # questions.
        #
        # `qs` is the MONEY. By month or by year that is the review sheet,
        # which is the record the business keeps and the one its YTD ACH
        # column is read off; for a date range it is the invoice dump, the
        # only one of the two that knows what day anything happened on.
        #
        # `detail` is the invoice dump alone, always. Everything below exists
        # only on an invoice -- orders, customers, SKUs, quantity, discount,
        # weight -- and the review sheet has none of them. Measured over `qs`
        # in month/year mode they would read zero against a real revenue
        # figure, and before that they were a ratio between two different
        # populations: total revenue over invoice count gave an average order
        # of Rs 30 lakh against a true Rs 1.8 lakh.
        qs, applied = apply_filters(SalesRecord.objects.all(), request)
        detail = detail_qs(request)

        agg = qs.aggregate(revenue=Sum('net_amount'), target=Sum('target_amount'),
                           lines=Count('id'))
        inv = detail.aggregate(
            revenue=Sum('measured_amount'),
            # An order is a Sales Order No., not an invoice number. One order
            # can be invoiced more than once, so counting invoices counted
            # the same order twice and made the average order value smaller
            # than it is. The empty-string filter stays: counting distinct
            # across everything also counts '' -- one phantom order standing
            # in for every row without one.
            orders=Count('sales_order_no', distinct=True,
                         filter=~Q(sales_order_no='')),
            qty=Sum('quantity'), discount=Sum('discount'),
            weight=Sum('net_weight_kg'))
        invoiced = {'revenue': inv['revenue'], 'orders': inv['orders']}
        # Cases, pieces, kilograms and metres all land in the same quantity
        # column, so the total is only meaningful once it is split by unit.
        uom_split = [
            {'unit': (r['uom'] or 'unspecified'),
             'quantity': _money(r['q']), 'lines': r['n']}
            for r in (detail.exclude(quantity=0).order_by().values('uom')
                        .annotate(q=Sum('quantity'), n=Count('id'))
                        .order_by('-n'))
        ]
        agg['qty'] = inv['qty']
        agg['discount'] = inv['discount']
        agg['weight'] = inv['weight']
        agg['orders'] = inv['orders']
        revenue = _money(agg['revenue'])
        target = _money(agg['target'])
        like_for_like = comparable_window(qs)
        # Which file answered the money question above. Every comparison
        # below has to be drawn from the same one, or the tile reads
        # "Rs 16 Cr, up 16% on last year" with the two halves measured off
        # different files -- which is the shape of the original complaint.
        from_dump = applied.get('source') == 'invoice_dump'
        cmp_base = money_base(request, dump_only=from_dump)
        # Deliberately ignores the date window, so narrowing to this year
        # does not take last year's comparison away with it. Withheld
        # entirely for a date range: the review sheet has no days, so there
        # is no "same fortnight last year" to answer with.
        last_year = None if from_dump else same_months_last_year(cmp_base)
        lo, hi = _period_bounds(qs)

        # Preceding window of identical length, same dimension filters, so the
        # comparison is like-for-like rather than "this quarter vs all history".
        prev = None
        if lo and hi:
            span = (hi - lo).days + 1
            p_hi = lo - timedelta(days=1)
            p_lo = p_hi - timedelta(days=span - 1)
            prev = (cmp_base.filter(order_date__gte=p_lo, order_date__lte=p_hi)
                       .aggregate(revenue=Sum('net_amount'), qty=Sum('quantity')))

        prev_rev = _money(prev['revenue']) if prev else 0.0
        # Customers are counted by code, not by name. Two branches of one
        # distributor write their name two ways and counted as two
        # customers; the code is the identity the ERP actually keys on.
        customers = (detail.exclude(customer_code='')
                     .values('customer_code').distinct().count())
        # SKUs are the finished goods only. The Item Code column also carries
        # raw material, packaging and consumable codes that ride on a sales
        # invoice without being something anyone sells, and counting them
        # inflated the SKU tile well past the number of products that exist.
        skus = (detail.filter(sku__istartswith=FINISHED_GOODS_PREFIX)
                .values('sku').distinct().count())
        orders = invoiced['orders'] or 0

        # Actual over the months that have actually happened, and what the
        # months still ahead would each have to do to land the plan. Both
        # read off the same like-for-like basis as achievement, so the three
        # figures describe one stretch of time rather than three.
        last_up = (SalesUpload.objects.filter(status='completed')
                   .order_by('-created_at').first())
        freshness = {
            'at': last_up.created_at.isoformat() if last_up else None,
            'file': last_up.filename if last_up else None,
            'by': (last_up.uploaded_by or None) if last_up else None,
            'uploads': SalesUpload.objects.filter(status='completed').count(),
        } if last_up else None

        run_rate = (round(like_for_like['revenue'] / like_for_like['months'], 2)
                    if like_for_like['months'] else None)

        # What is still owed on the plan, divided by the months left to owe
        # it in -- the blueprint's "remaining target gap / remaining
        # periods", and the gap is the operative word.
        #
        # This divided the remaining PLAN instead, which quietly forgave
        # every rupee already missed. Six months in at 79% of plan, it read
        # "needs Rs 30.74 Cr a month", when delivering exactly that lands the
        # year Rs 28.26 Cr short -- precisely the shortfall shown as "behind
        # by" two cards to its left. A pace figure that does not make up the
        # deficit is not the pace required to hit the target; it is the pace
        # required to miss it by the amount you are already missing it by.
        full_plan = like_for_like['target'] + like_for_like['target_ahead']
        still_owed = full_plan - like_for_like['revenue']
        required_rate = (round(max(0.0, still_owed) / like_for_like['months_ahead'], 2)
                         if like_for_like['months_ahead'] else None)

        return Response({
            'revenue': revenue,
            'quantity': _money(agg['qty']),
            # Quantity is counted in whatever unit each line was sold in --
            # 3,955 lines in cases, 241 in pieces, some in kg and some in
            # metres -- so the single figure above adds cases to kilograms
            # and means very little on its own. The split is sent alongside
            # it, and net weight is the one measure that is comparable across
            # all of them.
            'quantity_by_unit': uom_split,
            'mixed_units': len(uom_split) > 1,
            'net_weight_kg': _money(agg.get('weight')),
            'orders': orders,
            'lines': agg['lines'] or 0,
            'customers': customers,
            'skus': skus,
            'discount': _money(agg['discount']),
            # Over the invoiced rows only -- see `invoiced` above.
            'avg_order_value': (_money(float(invoiced['revenue'] or 0) / invoiced['orders'])
                                if invoiced['orders'] else 0.0),
            # What share of the money the per-order figures actually describe,
            # so the screen can say "of the invoiced months" rather than
            # implying it covers everything.
            'invoiced_revenue': _money(invoiced['revenue']),
            'invoiced_pct': (round(float(invoiced['revenue'] or 0) / revenue * 100, 1)
                             if revenue else None),
            'target': target,
            # Compared over the months that carry both a plan and a result.
            # Revenue and target above are the full totals for the current
            # filter and are NOT each other's denominator: they cover
            # different spans, and dividing one by the other is what put
            # 508,382% and then 86% on this dashboard.
            'achievement_pct': like_for_like['pct'],
            'achievement_basis': like_for_like,
            # Red / Amber / Green on the company's own rule -- see
            # sales/status.py, which also records what that rule does not
            # cover while only P1 is loaded.
            'status': STATUS.band(like_for_like['pct']),

            # Run rate, from the blueprint's KPI framework.
            #
            # It asks for these per WORKING DAY. They are per month here, and
            # that is a limit of the data rather than a choice: the review
            # sheet -- which is the record the business keeps, and the only
            # file carrying the plan -- has no days in it. Every row is a
            # month, stored on the 1st. A daily rate off it would be a
            # monthly figure divided by a number of days nobody measured,
            # presented to four significant figures. The month is the
            # smallest grain this file can honestly answer at, so that is
            # what is reported, named as such on screen.
            # When the figures on screen were last loaded, and from what.
            #
            # The blueprint's governance section asks for a visible "data
            # refreshed at", and it is the cheapest honest thing on the page:
            # every number here is as old as the last upload, and without
            # saying so a screen looks live when it may be a fortnight stale.
            # This product is uploaded to rather than connected to a feed, so
            # the refresh time IS the upload time.
            'data_refreshed': freshness,
            # Filters the invoice file has no column for. Everything built
            # out of invoice detail is empty under one of these, and empty
            # reads as "nothing sold" rather than "this file cannot say".
            'filters_blind_to_invoices': filters_the_dump_cannot_answer(applied),

            'run_rate': run_rate,
            'required_run_rate': required_rate,
            'run_rate_basis': {
                'per': 'month',
                'months_elapsed': like_for_like['months'],
                'months_ahead': like_for_like['months_ahead'],
                'target_ahead': like_for_like['target_ahead'],
                # The two numbers the required rate is made of, so it can be
                # checked rather than taken on trust.
                'full_plan': round(full_plan, 2),
                'still_owed': round(max(0.0, still_owed), 2),
                'already_ahead': still_owed < 0,
                'ahead_from': like_for_like['ahead_from'],
                'ahead_to': like_for_like['ahead_to'],
                # Whether the business has to lift its pace to land the plan,
                # which is the one thing the two rates are for.
                'lift_needed_pct': (round((required_rate / run_rate - 1) * 100, 1)
                                    if run_rate and required_rate else None),
            },
            # The comparison the business makes in its own sheet: this year
            # to date against the same months last year. The window above
            # ("prior period") is the stretch immediately before this one,
            # which for a seasonal business compares a festive quarter with a
            # quiet one; this lines April up with April.
            'vs_last_year': last_year,
            # The gap belongs to the same comparison as the percentage
            # beside it: plan for the months that have happened, less what
            # was sold in them. Against the full-year plan it read as a
            # shortfall of Rs 45 crore on a year that is half over.
            'gap_to_target': (round(like_for_like['target'] - like_for_like['revenue'], 2)
                              if like_for_like['target'] else None),
            'prev_revenue': prev_rev,
            # Whether there is anything BEHIND this window, as opposed to a
            # real zero. "vs Rs 0 prior period" reads as a collapse; most of
            # the time it means the file simply does not go back that far,
            # and the screen should say which.
            'prev_period_has_data': bool(prev and (prev['revenue'] or prev['qty'])),
            'revenue_growth_pct': _pct_change(revenue, prev_rev),
            'quantity_growth_pct': _pct_change(_money(agg['qty']),
                                               _money(prev['qty']) if prev else 0),
            'period': {'from': lo.isoformat() if lo else None,
                       'to': hi.isoformat() if hi else None},
            'filters': applied,
            'has_data': revenue != 0 or (agg['lines'] or 0) > 0,
            # How much is actually loaded, so the page can tell a real figure
            # from a near-empty table. A dashboard reading Rs 1.91 L off five
            # leftover rows is indistinguishable from a dashboard reading
            # Rs 274 Cr off the real file -- both look equally confident, and
            # that cost a morning of "are we picking the data correctly?".
            #
            # The threshold is deliberately low: the real file is 24,000-odd
            # lines, so anything in the dozens is leftovers or a part upload,
            # and nothing legitimate sits near it.
            'loaded': {
                'lines': SalesRecord.objects.count(),
                'uploads': SalesUpload.objects.filter(status='completed').count(),
                'looks_empty': SalesRecord.objects.count() < 100,
            },
        })


class SalesBreakdownView(SalesIQView):
    """Group by any whitelisted dimension. `?dim=state&metric=revenue&limit=10`"""

    def get(self, request):
        dim_key = (request.query_params.get('dim') or 'state').strip().lower()
        field = DIMENSIONS.get(dim_key)
        if not field:
            return Response({'error': f'Unknown dimension "{dim_key}".',
                             'available': sorted(DIMENSIONS.keys())}, status=400)
        metric = (request.query_params.get('metric') or 'revenue').strip().lower()
        try:
            limit = max(1, min(200, int(request.query_params.get('limit', 15))))
        except (TypeError, ValueError):
            limit = 15

        # Which file can answer a split by THIS column. The review sheet
        # carries zone, state, channel, head, RSM, ASM, item and brand;
        # customer, SKU, category, city and the rest live only on an invoice,
        # and a split by one of those read off the sheet comes back empty.
        qs, applied, from_dump = qs_for_dimension(request, field)
        # What the whole slice is worth BEFORE rows with nothing in this
        # column are dropped, so the chart can say what it is leaving out.
        grand = _money(qs.aggregate(v=Sum('net_amount'))['v'])
        qs = qs.exclude(**{field: ''})
        rows = (qs.values(field)
                  .annotate(revenue=Sum('net_amount'), quantity=Sum('quantity'),
                            target=Sum('target_amount'), orders=Count('invoice_no', distinct=True),
                            lines=Count('id'))
                  .order_by('-quantity' if metric == 'quantity' else '-revenue'))

        all_rows = list(rows)
        total_rev = sum(float(r['revenue'] or 0) for r in all_rows) or 1.0
        top = all_rows[:limit]
        out = []
        for r in top:
            rev = _money(r['revenue'])
            tgt = _money(r['target'])
            out.append({
                'name': r[field] or '—',
                'revenue': rev,
                'quantity': _money(r['quantity']),
                'orders': r['orders'],
                'lines': r['lines'],
                'target': tgt,
                'achievement_pct': round((rev / tgt) * 100, 1) if tgt else None,
                'status': STATUS.rag(round((rev / tgt) * 100, 1) if tgt else None),
                'share_pct': round((rev / total_rev) * 100, 1),
                # The reporting line carries the post, not the holder, so an
                # empty territory reads as somebody called "Vacant-Tri" and
                # was being ranked against real managers. Flagged rather than
                # dropped: the territory is still selling, and an unfilled
                # post with sales against it is worth seeing.
                'vacant': is_vacant(r[field] or ''),
            })
        others = all_rows[limit:]
        attributed = sum(float(r['revenue'] or 0) for r in all_rows)
        return Response({
            'dimension': dim_key,
            'metric': metric,
            'results': out,
            'total_groups': len(all_rows),
            # How much of the business this breakdown can actually speak for.
            # Neither file carries every column: the review sheet has no
            # customer or SKU, the ERP dump has no brand or sales head, and
            # national accounts -- Amazon, D-Mart, export -- sit in no state
            # at all. Without this the state chart quietly totalled 57% of
            # the headline and looked like it had lost Rs 118 crore.
            'coverage': {
                'attributed': round(attributed, 2),
                'total': grand,
                'unattributed': round(grand - attributed, 2),
                'pct': round(attributed / grand * 100, 1) if grand else None,
            },
            'others': {
                'count': len(others),
                'revenue': _money(sum(float(r['revenue'] or 0) for r in others)),
            } if others else None,
            # Why there is nothing here, when there is nothing here.
            'empty': why_empty(applied, from_dump, bool(out)),
            'filters': applied,
        })


class SalesTrendView(SalesIQView):
    """Monthly time series, with target and a cumulative running total."""

    # The whole point of a trend line is the months before this year.
    def get(self, request):
        qs, applied = apply_filters(SalesRecord.objects.all(), request,
                                    default_window=False)
        rows = (qs.values('period')
                  .annotate(revenue=Sum('net_amount'), quantity=Sum('quantity'),
                            measured=Sum('measured_amount'),
                            target=Sum('target_amount'), orders=Count('invoice_no', distinct=True))
                  .order_by('period'))
        out, running = [], 0.0
        prev_rev = None
        for r in rows:
            rev = _money(r['revenue'])
            tgt = _money(r['target'])
            # A month the business has not reached yet: a plan against it and
            # nothing measured. Reported as revenue of zero it drew the line
            # off a cliff for the rest of the financial year and made the
            # worst month of the year one that has not happened. Left as null
            # the line stops where the data stops, and the target bars carry
            # on beside it.
            pending = tgt > 0 and _money(r['measured']) == 0
            if pending:
                rev = None
            else:
                running += rev
            out.append({
                'period': r['period'].isoformat(),
                'label': r['period'].strftime('%b %Y'),
                'revenue': rev,
                'quantity': _money(r['quantity']),
                'orders': r['orders'],
                'target': tgt,
                'pending': pending,
                'achievement_pct': (round((rev / tgt) * 100, 1)
                                    if tgt and rev is not None else None),
                'cumulative': round(running, 2),
                'mom_growth_pct': (_pct_change(rev, prev_rev)
                                   if prev_rev is not None and rev is not None else None),
            })
            if rev is not None:
                prev_rev = rev

        scored = [r for r in out if r['revenue'] is not None]
        best = max(scored, key=lambda r: r['revenue']) if scored else None
        worst = min(scored, key=lambda r: r['revenue']) if scored else None
        return Response({
            'results': out, 'months': len(out),
            'best_month': best, 'worst_month': worst,
            'filters': applied,
        })


class SalesForecastView(SalesIQView):
    """Forecast future monthly revenue from the filtered history."""

    # A forecast is fitted on history; six months of it is not enough.
    def get(self, request):
        try:
            periods = max(1, min(24, int(request.query_params.get('periods', 6))))
        except (TypeError, ValueError):
            periods = 6
        metric = (request.query_params.get('metric') or 'revenue').strip().lower()
        agg_field = 'quantity' if metric == 'quantity' else 'net_amount'

        # DIMENSION filters only -- deliberately not the month window.
        #
        # The window says which months are being LOOKED at. It is not a
        # statement about which months the model may learn from, and reading
        # it as one did real damage: with Apr-Sep selected the forecast was
        # fitted on six months of a rising ramp, which dropped it out of the
        # seasonal model into a straight line and projected +78% on the next
        # six months. Two full years were loaded the whole time.
        #
        # Region, channel, category and the rest still apply: a forecast for
        # the North is a different series from a forecast for the company,
        # and that is the question being asked. A forecast for April to
        # September is not -- a forecast starts where the actuals stop.
        qs, applied = apply_dim_filters(SalesRecord.objects.all(), request)
        rows = (with_actuals(qs).values('period')
                .annotate(v=Sum(agg_field)).order_by('period'))
        points = [(r['period'], float(r['v'] or 0)) for r in rows]

        # ── the plan ──────────────────────────────────────────────────────
        # Loaded for the whole financial year, so it is known for months the
        # history has not reached. Only on revenue: a plan column in rupees
        # has nothing to say about a quantity forecast in cases.
        plan = {}
        if metric != 'quantity':
            plan = {r['period']: float(r['t'] or 0) for r in
                    qs.filter(source=SalesRecord.SOURCE_PLAN,
                              target_amount__gt=0)
                      .values('period').annotate(t=Sum('target_amount'))
                    if r['period']}

        # Anchored to the AOP wherever the plan reaches, and only falling
        # back to extrapolating the history where it does not.
        #
        # This reverses an earlier decision, and the reason is worth keeping:
        # the argument for holding the plan out was that the two lines should
        # be independent so the gap between them means something. What that
        # produced was a forecast with nothing holding it down -- it read the
        # slope of the months it was given and kept going, projecting a
        # second half well clear of anything the business had planned for,
        # which nobody in a review recognised as their own company. The gap
        # has not gone: it is now the run rate, stated as a percentage of
        # plan, with the months it was read from and the best and worst of
        # them. That is a gap somebody can argue with, which the old one was
        # not.
        result = forecast_from_plan(points, plan, periods=periods) \
            or forecast_series(points, periods=periods)
        result['metric'] = metric

        # The model is fitted on every month loaded -- more history is a
        # better fit, and that is what last year is loaded for. What goes on
        # the CHART starts at the display floor, so the forecast panel does
        # not reintroduce the year the rest of the dashboard stops showing.
        lo = display_floor()
        result['history'] = [{'period': d.isoformat(), 'label': d.strftime('%b %Y'),
                              'value': round(v, 2),
                              'aop': round(plan[d], 2) if d in plan else None}
                             for d, v in points if d >= lo]
        for pt in result.get('points') or []:
            month = date.fromisoformat(pt['period'])
            pt['label'] = month.strftime('%b %Y')
            pt.setdefault('aop', round(plan[month], 2) if month in plan else None)

        # Forecast against plan, over the months the plan actually reaches.
        # Totalled over those months alone -- comparing a six-month forecast
        # with four months of plan reads as a shortfall that is really just
        # a plan that stops in March.
        covered = [pt for pt in (result.get('points') or []) if pt.get('aop') is not None]
        if covered:
            fc_total = sum(pt['value'] for pt in covered)
            aop_total = sum(pt['aop'] for pt in covered)
            result['vs_aop'] = {
                'months': len(covered),
                'from': covered[0]['label'],
                'to': covered[-1]['label'],
                'forecast_total': round(fc_total, 2),
                'aop_total': round(aop_total, 2),
                'gap': round(fc_total - aop_total, 2),
                'cover_pct': round((fc_total / aop_total) * 100, 1) if aop_total else None,
                'partial': len(covered) < len(result.get('points') or []),
            }

        result['filters'] = applied
        # Said out loud, because the filter bar above this panel shows a
        # month range that this panel is ignoring on purpose.
        result['window_note'] = (
            'Fitted on every month loaded, not on the months selected above. '
            'The month filter narrows what the rest of the dashboard shows; '
            'a forecast has to learn from the full history and start where '
            'the actuals stop. Region, channel and the other filters do '
            'apply.')
        if result.get('method') == 'plan-anchored':
            result['window_note'] += (
                ' It runs to the end of the AOP and no further: past the '
                'plan there is no month shape to anchor to, and a straight '
                'line drawn out of the end of the year is not a forecast.')

        hist_total = sum(v for _, v in points)
        if points and result.get('points'):
            # Compare like with like: the same number of months, most recent first.
            n = min(periods, len(points))
            recent = sum(v for _, v in points[-n:])
            proj = sum(p['value'] for p in result['points'][:n])
            result['vs_recent'] = {
                'months': n,
                'recent_total': round(recent, 2),
                'projected_total': round(proj, 2),
                'change_pct': _pct_change(proj, recent),
            }
        result['history_total'] = round(hist_total, 2)
        return Response(result)


class SalesFiltersView(SalesIQView):
    """Distinct values for every filter, so the UI can populate its dropdowns."""

    def get(self, request):
        # Cancelled rows are excluded from every figure, so offering their
        # values here would put a customer in the dropdown that returns an
        # empty dashboard when picked.
        # Floored like every other on-screen read: a month, region or brand
        # that exists only in last year's rows must not be offered as
        # something to filter by, or the floor is a rule the dropdowns
        # quietly let you around.
        qs = (SalesRecord.objects.exclude(is_cancelled=True)
              .filter(order_date__gte=display_floor()))

        # The file that answers a figure also supplies the values you may
        # filter it by.
        #
        # Region and Sub-Region are stored in the same two columns the dump
        # writes North and a state code into, so the dropdowns listed both
        # vocabularies at once -- GTR01 beside North, CHD (TRI) beside 07.
        # Picking a dump value then filtered money that comes off the sheet
        # and emptied the dashboard. Same fault the channel had, where
        # DOMESTIC sat beside GT.
        #
        # So for the dimensions the sheet carries, offer the sheet's values
        # wherever it speaks for the months on screen. Everything else --
        # customer, SKU, city, invoice -- exists only on an invoice, and
        # reads the dump as before.
        covered = sheet_months()
        from_sheet = sheet_fields() if covered else set()
        sheet_qs = qs.exclude(source=SalesRecord.SOURCE_INVOICE)

        out = {}
        for f in FILTERABLE:
            base = sheet_qs if f in from_sheet else qs
            vals = (base.exclude(**{f: ''}).values_list(f, flat=True)
                        .order_by(f).distinct()[:500])
            out[f] = list(vals)
        lo, hi = _period_bounds(qs)
        out['date_range'] = {'from': lo.isoformat() if lo else None,
                             'to': hi.isoformat() if hi else None}
        # The months the data actually has, newest first, for the window
        # pickers. A free month input let somebody choose a month no file
        # covers and read the empty dashboard as a bad month of trading;
        # offering only what is here cannot say that.
        out['months'] = [d.strftime('%Y-%m') for d in
                         qs.exclude(period=None).order_by('-period')
                           .values_list('period', flat=True).distinct()]
        out['dimensions'] = sorted(DIMENSIONS.keys())
        # Which of them this upload can actually answer. Neither primary file
        # carries Area, Region, Territory or Salesperson at all, and Sub
        # Category and Variant are present but empty, so six breakdowns were
        # being offered and then rendering as blank panels with no
        # explanation. The UI hides what is not here.
        available, absent = [], []
        for key, field in DIMENSIONS.items():
            (available if qs.exclude(**{field: ''}).exists() else absent).append(key)
        out['available_dimensions'] = sorted(available)
        out['absent_dimensions'] = sorted(absent)
        # Why each absent one is absent, because the two reasons want
        # opposite actions and the screen was giving one answer to both.
        #
        # Area, Region, Territory and Salesperson are in neither file: adding
        # the column is exactly right. Sub Category and Variant ARE columns in
        # the Pre-Sales Dump -- they come through on every row and are blank
        # on every row -- so "add this column and re-upload" sends somebody to
        # add a column that is already there, and nothing changes when they
        # do. That one wants filling in upstream, in the ERP.
        declared = _declared_dimension_keys()
        out['absent_detail'] = [
            {'dim': k,
             'reason': 'empty' if k in declared else 'missing'}
            for k in sorted(absent)
        ]
        return Response(out)


def _declared_dimension_keys():
    """Dimension keys the Pre-Sales Dump has a column for, whether or not
    anybody fills it in.

    Read off the file contract rather than hard-coded, so a column added to
    PRE_SALES_DUMP is accounted for here without anyone remembering to.
    """
    from ..ingest import COLUMN_ALIASES, PRE_SALES_DUMP, _norm

    headers = {_norm(h.rstrip(' *')) for h, _ in PRE_SALES_DUMP}
    out = set()
    for key, field in DIMENSIONS.items():
        for alias in COLUMN_ALIASES.get(field, []):
            if _norm(alias) in headers:
                out.add(key)
                break
    return out


class SalesInsightsView(SalesIQView):
    """Auto-generated written observations — the 'so what' the numbers imply.

    Kept server-side so the same wording appears everywhere the data is shown."""

    def get(self, request):
        qs, applied = apply_filters(SalesRecord.objects.all(), request)
        insights = []

        total = _money(qs.aggregate(v=Sum('net_amount'))['v'])
        if not total:
            return Response({'insights': [], 'filters': applied})

        def top_of(field, label, min_share=0):
            rows = (qs.exclude(**{field: ''}).values(field)
                      .annotate(v=Sum('net_amount')).order_by('-v')[:3])
            rows = [r for r in rows if r['v']]
            if not rows:
                return None
            share = float(rows[0]['v']) / total * 100
            if share < min_share:
                return None
            return rows, share

        # Concentration risk — one region/customer carrying too much of the book.
        for field, noun in (('state', 'state'), ('customer_name', 'customer'),
                            ('category', 'category')):
            got = top_of(field, noun)
            if not got:
                continue
            rows, share = got
            name = rows[0][field]
            if share >= 40:
                insights.append({
                    'type': 'risk',
                    'title': f'Heavy concentration in one {noun}',
                    'body': f'{name} alone accounts for {share:.0f}% of sales. '
                            f'That is a single point of failure — losing it would take a '
                            f'large share of revenue with it.',
                })
            elif share >= 25:
                insights.append({
                    'type': 'info',
                    'title': f'Top {noun}: {name}',
                    'body': f'{name} contributes {share:.0f}% of total sales — the largest '
                            f'single {noun} in this selection.',
                })

        # Target achievement, over the months that carry BOTH a plan and a
        # result -- the same basis as the headline tile, via the same
        # function. This block used to divide revenue-to-date by the whole
        # year's plan, which is the very bug comparable_window was written to
        # kill: it reported 86% and a shortfall of Rs 45 Cr on a year that is
        # half over, while the tile four inches above it said 73% and
        # Rs 36 Cr. Two numbers for one question on one screen is worse than
        # either of them being wrong.
        basis = comparable_window(qs)
        tgt = basis['target']
        total_cmp = basis['revenue']
        if tgt:
            ach = total_cmp / tgt * 100
            if ach >= 100:
                insights.append({
                    'type': 'win',
                    'title': f'Target exceeded — {ach:.0f}%',
                    'body': f'Sales of {total_cmp:,.0f} against a target of {tgt:,.0f} '
                            f'over the {basis["months"]} month(s) with both, '
                            f'ahead by {total_cmp - tgt:,.0f}.',
                })
            else:
                insights.append({
                    'type': 'risk' if ach < 80 else 'info',
                    'title': f'Target achievement at {ach:.0f}%',
                    'body': f'Short of target by {tgt - total_cmp:,.0f} '
                            f'over the {basis["months"]} month(s) with both a plan '
                            f'and a result. '
                            f'{"Well behind plan — worth investigating by region." if ach < 80 else "Within reach of plan."}',
                })
            # Who is dragging
            lag = (qs.exclude(salesperson='').values('salesperson')
                     .annotate(rev=Sum('net_amount'), t=Sum('target_amount'))
                     .filter(t__gt=0).order_by('rev'))
            lag = [r for r in lag if float(r['rev']) / float(r['t']) < 0.7][:3]
            if lag:
                names = ', '.join(f"{r['salesperson']} ({float(r['rev'])/float(r['t'])*100:.0f}%)"
                                  for r in lag)
                insights.append({
                    'type': 'risk',
                    'title': 'Sales people below 70% of target',
                    'body': f'{names}. These are the biggest gaps to close.',
                })

        # Momentum from the monthly series
        months = list(with_actuals(qs).values('period')
                      .annotate(v=Sum('net_amount')).order_by('period'))
        if len(months) >= 4:
            recent = [float(m['v'] or 0) for m in months[-3:]]
            earlier = [float(m['v'] or 0) for m in months[-6:-3]] or recent
            r_avg, e_avg = sum(recent) / len(recent), sum(earlier) / len(earlier)
            if e_avg:
                delta = (r_avg - e_avg) / e_avg * 100
                if abs(delta) >= 10:
                    insights.append({
                        'type': 'win' if delta > 0 else 'risk',
                        'title': f'Momentum {"rising" if delta > 0 else "falling"} '
                                 f'{abs(delta):.0f}%',
                        'body': f'The last 3 months average {r_avg:,.0f} vs {e_avg:,.0f} in the '
                                f'3 months before — a clear '
                                f'{"upswing" if delta > 0 else "slowdown"}.',
                    })

        # Discount pressure
        disc = _money(qs.aggregate(v=Sum('discount'))['v'])
        gross = _money(qs.aggregate(v=Sum('gross_amount'))['v'])
        if gross and disc / gross * 100 >= 12:
            insights.append({
                'type': 'risk',
                'title': f'Discounting at {disc / gross * 100:.0f}% of gross',
                'body': f'{disc:,.0f} given away against {gross:,.0f} gross. '
                        f'High discount intensity erodes margin even when top-line looks healthy.',
            })

        # Dormant customers — bought before, nothing recently.
        lo, hi = _period_bounds(qs)
        if hi:
            cutoff = hi - timedelta(days=90)
            # Keyed on the customer CODE, the way every other customer
            # count in this app is. On the name, one account spelled two
            # ways was two customers -- and a renamed account was reported
            # dormant while it was still buying under its new spelling.
            def _codes(rows):
                return {c: n for c, n in rows}
            recent_c = _codes(qs.filter(order_date__gt=cutoff)
                                .exclude(customer_code='')
                                .values_list('customer_code', 'customer_name')
                                .order_by().distinct())
            all_c = _codes(qs.exclude(customer_code='')
                             .values_list('customer_code', 'customer_name')
                             .order_by().distinct())
            dormant = {all_c[c] or c for c in (set(all_c) - set(recent_c))}
            if dormant and len(all_c) >= 5:
                insights.append({
                    'type': 'risk',
                    'title': f'{len(dormant)} customer(s) inactive for 90+ days',
                    'body': f'{", ".join(sorted(dormant)[:5])}'
                            f'{" and others" if len(dormant) > 5 else ""} have not ordered in the '
                            f'last 90 days of this period. Worth a win-back call.',
                })

        return Response({'insights': insights, 'filters': applied})


class SalesUploadsView(SalesIQView):
    """List uploads; delete one (rolls back a bad file) or all."""

    def get(self, request):
        ups = SalesUpload.objects.all()[:100]
        return Response({'results': [{
            'id': u.id, 'filename': u.filename, 'rows': u.row_count,
            'notes': u.notes,
            'skipped': u.skipped_rows, 'revenue': _money(u.total_revenue),
            'period_start': u.period_start.isoformat() if u.period_start else None,
            'period_end': u.period_end.isoformat() if u.period_end else None,
            'warnings': u.warnings, 'status': u.status,
            'created_at': u.created_at.isoformat(),
        } for u in ups],
            'total_rows': SalesRecord.objects.count(),
            'count': SalesUpload.objects.count()})

    def delete(self, request):
        # The one destructive action in SalesIQ. Readers may see which files
        # are loaded -- that is the provenance of every figure on the screen
        # -- but not remove them.
        self.require_owner(request)
        up_id = request.query_params.get('id')
        if up_id:
            try:
                u = SalesUpload.objects.get(id=up_id)
            except SalesUpload.DoesNotExist:
                return Response({'error': 'Upload not found'}, status=404)
            n = u.records.count()
            u.delete()   # cascades to its rows
            # Removing the invoice data hands the figures back to the AOP
            # sheet, if one is loaded — otherwise the dashboard would go to
            # zero while a perfectly good plan file sat in the table.
            sync_actual_source()
            return Response({'message': f'Removed upload "{u.filename}" and {n:,} row(s).',
                             'deleted': n})
        n = SalesRecord.objects.count()
        SalesRecord.objects.all().delete()
        SalesUpload.objects.all().delete()
        return Response({'message': f'Cleared all sales data ({n:,} row(s)).', 'deleted': n})


class SalesExportView(SalesIQView):
    """Export the current filtered view as Excel — summary + per-dimension sheets."""

    def get(self, request):
        qs, applied = apply_filters(SalesRecord.objects.all(), request)
        wb = openpyxl.Workbook()
        from openpyxl.styles import Font as F, PatternFill as P

        def sheet(title, header, rows):
            ws = wb.create_sheet(title[:31])
            ws.append(header)
            for c in ws[1]:
                c.font = F(bold=True, color='FFFFFF')
                c.fill = P(start_color='1F4E79', end_color='1F4E79', fill_type='solid')
            for r in rows:
                ws.append(r)
            for i, _ in enumerate(header, 1):
                ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = 22
            ws.freeze_panes = 'A2'

        agg = qs.aggregate(rev=Sum('net_amount'), qty=Sum('quantity'),
                           tgt=Sum('target_amount'), lines=Count('id'))
        ws = wb.active
        ws.title = 'Summary'
        ws.append(['SalesIQ Export'])
        ws['A1'].font = F(bold=True, size=14)
        ws.append([])
        for k, v in (('Total Revenue', _money(agg['rev'])),
                     ('Total Quantity', _money(agg['qty'])),
                     ('Total Target', _money(agg['tgt'])),
                     ('Rows', agg['lines'] or 0)):
            ws.append([k, v])
        ws.append([])
        ws.append(['Filters applied'])
        for k, v in (applied or {'(none)': ''}).items():
            ws.append([k, ', '.join(v) if isinstance(v, list) else str(v)])
        ws.column_dimensions['A'].width = 24
        ws.column_dimensions['B'].width = 40

        for dim_key in ('state', 'area', 'category', 'product', 'channel', 'salesperson',
                        'customer'):
            field = DIMENSIONS[dim_key]
            rows = (qs.exclude(**{field: ''}).values(field)
                      .annotate(rev=Sum('net_amount'), qty=Sum('quantity'),
                                tgt=Sum('target_amount'))
                      .order_by('-rev')[:500])
            if not rows:
                continue
            sheet(dim_key.title(), [dim_key.title(), 'Revenue', 'Quantity', 'Target',
                                    'Achievement %'],
                  [[r[field], _money(r['rev']), _money(r['qty']), _money(r['tgt']),
                    round(float(r['rev'] or 0) / float(r['tgt']) * 100, 1) if r['tgt'] else '']
                   for r in rows])

        trend = (qs.values('period').annotate(rev=Sum('net_amount'), qty=Sum('quantity'),
                                              tgt=Sum('target_amount')).order_by('period'))
        if trend:
            sheet('Monthly Trend', ['Month', 'Revenue', 'Quantity', 'Target'],
                  [[r['period'].strftime('%b %Y'), _money(r['rev']), _money(r['qty']),
                    _money(r['tgt'])] for r in trend])

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        resp = HttpResponse(
            buf.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = 'attachment; filename="SalesIQ_Export.xlsx"'
        return resp

