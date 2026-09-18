"""Excel template + tolerant parsing for SalesIQ uploads.

Real sales exports never match a fixed schema — the same field appears as
"State", "STATE NAME" or "Billing State" depending on who ran the report. So
headers are matched against an alias table rather than required verbatim, and
anything unrecognised is reported back instead of silently dropped.
"""
import io
import re
from datetime import datetime, date

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

# Canonical field -> every header spelling seen in the wild. Matching is done
# on a normalised key (lowercased, punctuation stripped).
COLUMN_ALIASES = {
    'order_date':    ['order date', 'date', 'invoice date', 'bill date', 'txn date',
                      'transaction date', 'sale date', 'posting date', 'month'],
    'invoice_date':  ['invoice date'],
    'posting_date':  ['posting date'],
    # 'type' alone is the LINE type in this export (Item / G/L Account),
    # not the document type. Mapping it to document_type made every row
    # look like a document kind nobody recognised.
    'line_type':     ['type', 'line type'],
    'document_type': ['document type', 'doc type', 'entry type'],
    'is_cancelled':  ['cancelled', 'canceled', 'is cancelled', 'cancel'],
    'external_doc_no': ['external doc no', 'external document no', 'ext doc no'],
    'sales_order_no':  ['sales order no', 'so no', 'sales order number'],
    'reason_code':     ['reason code'],
    'remarks':         ['v remars', 'v remarks', 'remarks', 'remark', 'narration'],
    'invoice_no':    ['invoice no', 'invoice number', 'invoice', 'bill no', 'bill number',
                      'document no', 'order no', 'order number'],
    'zone':          ['zone', 'sales zone'],
    'state':         ['state', 'state name', 'billing state', 'customer state'],
    'city':          ['city', 'town', 'city name', 'billing city', 'customer city'],
    'subzone':       ['subzone', 'sub zone'],
    'customer_district':   ['customer district', 'cust district'],
    'customer_state_code': ['cust state code', 'customer state code', 'state code'],
    'customer_gst':        ['cust gst reg', 'customer gst reg', 'customer gstin', 'gstin'],
    'business_type':  ['customer posting group business type', 'customer posting group',
                       'business type', 'cust posting group'],
    'warehouse_type': ['customer price group warehousetype', 'customer price group',
                       'customer price group warehouse type', 'warehouse type'],
    'location':       ['location', 'branch', 'depot', 'plant'],
    'location_state': ['location state'],
    'area':          ['area', 'area name', 'district', 'beat', 'sales area'],
    'region':        ['region', 'region name'],
    'sku':           ['sku', 'sku code', 'item code', 'product code', 'material code',
                      'article code', 'item no'],
    'product_name':  ['product', 'product name', 'item name', 'item description',
                      'material description', 'description'],
    'category':      ['category', 'product category', 'item category', 'segment'],
    'sub_category':  ['sub category', 'subcategory', 'sub segment', 'product sub category',
                      'item sub category sub brand', 'item sub category'],
    'brand':         ['brand', 'brand name'],
    'pack_size':     ['pack size', 'pack', 'size', 'grammage', 'weight'],
    'uom':           ['uom', 'unit', 'unit of measure', 'unit of measure code'],
    'item_alt_code':  ['i code', 'icode', 'alt item code'],
    'packaging_type': ['packeging type', 'packaging type', 'pack type'],
    'item_sub_type':  ['item sub type'],
    'variant':        ['variant'],
    'prod_group':     ['prod group', 'product group'],
    'hsn_code':       ['hsn code', 'hsn', 'hsn sac'],
    'batch_no':       ['batch no', 'batch', 'batch number', 'lot no'],
    'packed_on':      ['pkd', 'packed on', 'packing date', 'mfg date'],
    'use_by':         ['use by', 'expiry', 'expiry date', 'best before'],
    'mrp':            ['mrp'],
    'gross_weight_kg': ['gross weight in kg', 'gross weight'],
    'net_weight_kg':   ['net weight in kg', 'net weight'],
    'channel':       ['channel', 'sales channel', 'trade channel', 'route to market',
                      'gen bus posting group'],
    'customer_code': ['customer code', 'customer no', 'party code', 'distributor code',
                      'dealer code', 'buyer code', 'account code'],
    'customer_name': ['customer', 'customer name', 'party name', 'distributor',
                      'distributor name', 'dealer', 'dealer name', 'buyer', 'account name'],
    'customer_type': ['customer type', 'party type', 'account type'],
    'salesperson':   ['salesperson', 'sales person', 'sales executive', 'se', 'so',
                      'sales officer', 'executive', 'employee name', 'sales rep'],
    'asm':           ['asm', 'asm name', 'area sales manager', 'area manager'],
    'rsm':           ['rsm', 'rsm name', 'regional sales manager', 'regional manager'],
    'territory':     ['territory', 'territory name', 'beat name'],
    'quantity':      ['quantity', 'qty', 'sales qty', 'billed qty', 'volume', 'units'],
    # 'mrp' deliberately absent: the dump carries Unit Price AND MRP as
    # separate columns, and they are different numbers — the price actually
    # charged versus the price printed on the pack.
    'unit_price':    ['unit price', 'rate', 'price', 'selling price'],
    'gross_amount':  ['gross amount', 'gross', 'gross value', 'gross sales'],
    'discount':      ['discount', 'discount amount', 'scheme', 'scheme amount'],
    'tax':           ['tax', 'gst', 'tax amount', 'gst amount'],
    'net_amount':    ['net amount', 'net sales', 'net value', 'amount', 'sales value',
                      'sales', 'revenue', 'total', 'net revenue', 'value', 'net'],
    'target_amount': ['target', 'target amount', 'budget', 'budget amount', 'plan',
                      'target value', 'goal'],

    # Pre-Sales Dump money columns. The percentage columns beside each of
    # these (Invoice Disc.%, Retail Scheme %, IGST %, TCS % and the rest)
    # are deliberately not read: every one is derivable from its own amount
    # and the taxable value, and a stored percentage that disagrees with the
    # amount next to it is a number nobody can act on.
    'taxable_amount':   ['taxable amount', 'taxable value'],
    'invoice_discount': ['invoice disc amt', 'invoice discount amount',
                         'invoice disc amount'],
    'retail_scheme':    ['retail scheme amt', 'retail scheme amount'],
    'wholesale_scheme': ['wholesale scheme amt', 'wholesale scheme amount'],
    'igst_amount':      ['igst amount', 'igst amt'],
    'cgst_amount':      ['cgst amount', 'cgst amt'],
    'sgst_amount':      ['sgst amount', 'sgst amt'],
    'tcs_amount':       ['tcs amount', 'tcs amt'],
    'total_with_tax':   ['total line amount gst tcs', 'total line amount'],
    'gst_jurisdiction': ['gst jurisdiction type', 'gst jurisdiction'],
    'currency_code':    ['currency code', 'currency'],
    'exchange_rate':    ['exchange rate'],
    'line_amount_fc':   ['line amount fc', 'line amount foreign currency'],
}

TEXT_FIELDS = ['invoice_no', 'zone', 'state', 'city', 'area', 'region', 'sku',
               'product_name', 'category', 'sub_category', 'brand', 'pack_size', 'uom',
               'channel', 'customer_code', 'customer_name', 'customer_type',
               'salesperson', 'asm', 'rsm', 'territory',
               # Pre-Sales Dump
               'document_type', 'line_type', 'external_doc_no', 'sales_order_no',
               'reason_code',
               'remarks', 'customer_district', 'customer_state_code', 'customer_gst',
               'business_type', 'warehouse_type', 'subzone', 'location', 'location_state',
               'item_alt_code', 'packaging_type', 'item_sub_type', 'variant',
               'prod_group', 'hsn_code', 'batch_no', 'gst_jurisdiction', 'currency_code']
NUM_FIELDS = ['quantity', 'unit_price', 'gross_amount', 'discount', 'tax',
              'net_amount', 'target_amount',
              # Pre-Sales Dump
              'taxable_amount', 'invoice_discount', 'retail_scheme', 'wholesale_scheme',
              'igst_amount', 'cgst_amount', 'sgst_amount', 'tcs_amount',
              'total_with_tax', 'mrp', 'gross_weight_kg', 'net_weight_kg',
              'exchange_rate', 'line_amount_fc']

# Parsed with parse_date rather than parse_num, and nullable — an absent
# expiry is not the same as 1970.
DATE_FIELDS = ['invoice_date', 'posting_date', 'packed_on', 'use_by']

# Max chars per text field, mirroring the model's max_length values so a long
# free-text cell truncates instead of raising a DB "Data too long" error.
TEXT_MAX = {
    'invoice_no': 100, 'zone': 100, 'state': 100, 'city': 100, 'area': 150, 'region': 100,
    'sku': 100, 'product_name': 255, 'category': 150, 'sub_category': 150, 'brand': 150,
    'pack_size': 80, 'uom': 40, 'channel': 100, 'customer_code': 100, 'customer_name': 255,
    'customer_type': 100, 'salesperson': 200, 'asm': 200, 'rsm': 200, 'territory': 150,
    'document_type': 60, 'line_type': 60, 'external_doc_no': 100, 'sales_order_no': 100, 'reason_code': 80,
    'remarks': 500, 'customer_district': 150, 'customer_state_code': 20, 'customer_gst': 30,
    'business_type': 100, 'warehouse_type': 100, 'subzone': 100, 'location': 150,
    'location_state': 100, 'item_alt_code': 100, 'packaging_type': 100, 'item_sub_type': 100,
    'variant': 150, 'prod_group': 150, 'hsn_code': 40, 'batch_no': 80,
    'gst_jurisdiction': 40, 'currency_code': 10,
}

# Spellings of "yes" seen in ERP exports. Anything else — including a blank,
# a 0, or the word "No" — means not cancelled. Deliberately a whitelist: a
# cancelled invoice wrongly treated as live overstates sales, so an
# unrecognised value must not accidentally read as true.
TRUTHY = {'yes', 'y', 'true', '1', 'cancelled', 'canceled', 'x'}

# A credit memo is a return: money going back out. The dump carries them in
# the same sheet as live invoices.
RETURN_TYPES = {'credit memo', 'credit note', 'return', 'sales credit memo',
                'crmemo', 'cr memo'}


# GST state codes -> state name.
#
# The Pre-Sales Dump has no plain "State" column: it carries Cust.State Code
# (the GST code, "07") and Location State (the depot's state, which is a
# different thing entirely). Without this table every dump row would land with
# an empty state, and the state-wise view — the dashboard's default — would
# come up blank on a file that plainly knows where every sale went.
#
# These codes are statutory and fixed, so a lookup is safe in a way that
# guessing at a spelling never is.
GST_STATE_CODES = {
    '01': 'Jammu & Kashmir', '02': 'Himachal Pradesh', '03': 'Punjab',
    '04': 'Chandigarh', '05': 'Uttarakhand', '06': 'Haryana', '07': 'Delhi',
    '08': 'Rajasthan', '09': 'Uttar Pradesh', '10': 'Bihar', '11': 'Sikkim',
    '12': 'Arunachal Pradesh', '13': 'Nagaland', '14': 'Manipur', '15': 'Mizoram',
    '16': 'Tripura', '17': 'Meghalaya', '18': 'Assam', '19': 'West Bengal',
    '20': 'Jharkhand', '21': 'Odisha', '22': 'Chhattisgarh', '23': 'Madhya Pradesh',
    '24': 'Gujarat', '25': 'Daman & Diu', '26': 'Dadra & Nagar Haveli and Daman & Diu',
    '27': 'Maharashtra', '29': 'Karnataka', '30': 'Goa', '31': 'Lakshadweep',
    '32': 'Kerala', '33': 'Tamil Nadu', '34': 'Puducherry',
    '35': 'Andaman & Nicobar Islands', '36': 'Telangana', '37': 'Andhra Pradesh',
    '38': 'Ladakh', '97': 'Other Territory', '99': 'Centre Jurisdiction',
}


# The other spelling of a state code. ERP exports use these at least as often
# as the numeric GST ones, and a dump carrying "AP" rather than "37" was
# leaving every row with no state at all — which emptied the state-wise view
# and let the AOP sheet's sub-regions be the only thing in it.
ALPHA_STATE_CODES = {
    'AP': 'Andhra Pradesh', 'AR': 'Arunachal Pradesh', 'AS': 'Assam',
    'BR': 'Bihar', 'CG': 'Chhattisgarh', 'CT': 'Chhattisgarh', 'GA': 'Goa',
    'GJ': 'Gujarat', 'HR': 'Haryana', 'HP': 'Himachal Pradesh',
    'JH': 'Jharkhand', 'JK': 'Jammu & Kashmir', 'KA': 'Karnataka',
    'KL': 'Kerala', 'LA': 'Ladakh', 'LD': 'Lakshadweep', 'MP': 'Madhya Pradesh',
    'MH': 'Maharashtra', 'MN': 'Manipur', 'ML': 'Meghalaya', 'MZ': 'Mizoram',
    'NL': 'Nagaland', 'OD': 'Odisha', 'OR': 'Odisha', 'PB': 'Punjab',
    'PY': 'Puducherry', 'RJ': 'Rajasthan', 'SK': 'Sikkim', 'TN': 'Tamil Nadu',
    'TS': 'Telangana', 'TG': 'Telangana', 'TR': 'Tripura', 'UK': 'Uttarakhand',
    'UA': 'Uttarakhand', 'UP': 'Uttar Pradesh', 'WB': 'West Bengal',
    'AN': 'Andaman & Nicobar Islands', 'CH': 'Chandigarh', 'DL': 'Delhi',
    'DN': 'Dadra & Nagar Haveli and Daman & Diu', 'DD': 'Daman & Diu',
}


# ── one spelling per person ───────────────────────────────────────────────
#
# The two primary-sales files disagree about case. The review sheet shouts
# (VAIBHAV MISHRA, MOHINDER SHARMA); the ERP dump does not (Vaibhav Mishra,
# Mohinder Sharma). Grouped as written, one person becomes two rows on every
# leaderboard with their sales split between them, and two separate branches
# of the org tree -- and nothing on screen suggests the two are the same
# person. Eight people and accounts in the first real file were split this
# way, including a national sales head.
#
# Folding to one case would group them but would also shout the names back at
# the user; title case groups them and reads like a name. It is applied to who
# somebody is, never to what they sold: an item code is case-sensitive and a
# product name is the brand's to spell.
NAME_FIELDS = ('salesperson', 'asm', 'rsm', 'sales_head', 'customer_name')


def normalise_name(value):
    """A person or account name, spelled one way."""
    text = ' '.join(str(value or '').split())
    return text.title() if text else ''


def state_from_code(value):
    """A state code in any of the spellings these exports use -> a state name.

    Numeric GST codes ('07', 7, or a full GSTIN whose first two characters
    are the code) and the two-letter forms ('DL', 'AP') both resolve.

    An unrecognised code is returned as-is rather than dropped. A state-wise
    view listing "AP" is imperfect; one that silently omits every row from
    that state is wrong, and the row had told us where it went.
    """
    if value is None:
        return ''
    s = str(value).strip()
    if not s:
        return ''
    # Excel turns a text code into a number and drops the leading zero.
    if s.replace('.0', '').isdigit():
        s = f'{int(float(s)):02d}'
        return GST_STATE_CODES.get(s[:2], s)
    upper = s.upper()
    if upper in ALPHA_STATE_CODES:
        return ALPHA_STATE_CODES[upper]
    # A GSTIN starts with its numeric state code.
    if len(upper) >= 2 and upper[:2].isdigit():
        return GST_STATE_CODES.get(upper[:2], upper[:2])
    if upper[:2] in ALPHA_STATE_CODES:
        return ALPHA_STATE_CODES[upper[:2]]
    return s


def state_from_subregion(value):
    """'AP-1' -> 'Andhra Pradesh'. Anything that is not a state code -> ''.

    The AOP sheet's Sub-Region is a selling territory, not a state: AP-1 and
    AP-2 are two patches of Andhra Pradesh, and some rows carry a customer
    name there instead ("Amazon"). Storing those verbatim as states put
    "AP-1" and "Amazon" in the state-wise view alongside Delhi and
    Maharashtra, which is two vocabularies in one column.

    Strict on purpose: only a recognised two-letter or numeric state code
    before the separator resolves. A sub-region that names something else
    keeps its own column and leaves state empty rather than inventing one.
    """
    if value is None:
        return ''
    raw = str(value).strip()
    if not raw:
        return ''
    head = raw.replace('_', '-').split('-')[0].strip()
    if not head:
        return ''
    # Some sheets write the state out in full rather than as a code.
    by_name = {n.lower(): n for n in
               list(ALPHA_STATE_CODES.values()) + list(GST_STATE_CODES.values())}
    if head.lower() in by_name:
        return by_name[head.lower()]
    upper = head.upper()
    if upper in ALPHA_STATE_CODES:
        return ALPHA_STATE_CODES[upper]
    if upper.isdigit():
        return GST_STATE_CODES.get(f'{int(upper):02d}', '')
    return ''


def parse_bool(v):
    """Is this cell saying yes?"""
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return str(v).strip().lower() in TRUTHY


def is_return_type(v):
    """Does this document type mean stock came back?"""
    s = _norm(v)
    return bool(s) and (s in RETURN_TYPES or 'credit memo' in s or s.startswith('return'))


def _norm(h):
    """Normalise a header cell for alias matching."""
    s = str(h or '').strip().lower().replace('*', '')
    # '&' belongs here too: 'Total Line Amount GST & TCS' otherwise keeps
    # its ampersand and matches no alias anyone would think to write.
    for ch in ('_', '-', '.', '/', '(', ')', ':', '&'):
        s = s.replace(ch, ' ')
    return ' '.join(s.split())


_LOOKUP = {}
for field, aliases in COLUMN_ALIASES.items():
    for a in aliases:
        _LOOKUP[_norm(a)] = field


# How far down to look for the header row. Report sheets routinely carry a
# title, a blank line, or a "generated on" stamp above the real headers.
HEADER_SCAN_ROWS = 15


def find_header_row(ws, recognises=None):
    """-> (header tuple, 1-based row index), or (None, 0) for an empty sheet.

    Picks the row in the first few that most looks like headers, rather than
    trusting row 1. A title row above the table used to be read AS the table,
    which then failed with "no date column" — an accurate complaint about the
    wrong row, and an impossible one to act on.
    """
    recognises = recognises or (lambda row: len(map_headers(row)[0]))
    best, best_row, best_score = None, 0, 0
    for idx, row in enumerate(ws.iter_rows(min_row=1, max_row=HEADER_SCAN_ROWS,
                                           values_only=True), start=1):
        if not row or not any(v is not None and str(v).strip() != '' for v in row):
            continue
        score = recognises(row)
        if score > best_score:
            best, best_row, best_score = row, idx, score
        # Two or more recognised columns is already a header row; stop rather
        # than letting a data row further down score higher by coincidence.
        if best_score >= 2 and idx - best_row >= 2:
            break
    if best is None:
        # Nothing matched. Fall back to the first non-empty row so the caller
        # can report what it actually found there.
        for idx, row in enumerate(ws.iter_rows(min_row=1, max_row=HEADER_SCAN_ROWS,
                                               values_only=True), start=1):
            if row and any(v is not None and str(v).strip() != '' for v in row):
                return row, idx
        return None, 0
    return best, best_row


def map_headers(header_row):
    """-> (field -> column index, list of unrecognised header names)."""
    col_map, unknown = {}, []
    for ci, cell in enumerate(header_row):
        if cell is None or str(cell).strip() == '':
            continue
        key = _norm(cell)
        field = _LOOKUP.get(key)
        if field is None:
            unknown.append(str(cell).strip())
        elif field not in col_map:      # first occurrence wins
            col_map[field] = ci
    return col_map, unknown


_ISO_RE = re.compile(r'^\d{4}-\d{1,2}-\d{1,2}(?:[T ]|$)')


def parse_date(v):
    """Parse a date from Excel cells OR API query params.

    ISO (YYYY-MM-DD) is checked FIRST and parsed strictly. dateutil with
    dayfirst=True reads "2026-02-01" as 2 January, not 1 February — which
    silently corrupted every date-range filter coming from an <input type=
    "date"> whenever the day was <= 12. Indian sheets still need dayfirst for
    "05-04-2026", so both rules coexist: ISO wins when the shape is ISO.
    """
    if v is None or str(v).strip() == '':
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if _ISO_RE.match(s):
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            pass    # e.g. 2026-13-45 — fall through to the tolerant parser
    try:
        from dateutil import parser as _p
        return _p.parse(s, dayfirst=True).date()
    except Exception:
        return None


def parse_num(v, default=0.0):
    """Tolerant of Excel text cells: '1,234.50', '₹1,234', '(500)' negatives, '12%'."""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return default
    neg = s.startswith('(') and s.endswith(')')
    for ch in ('₹', 'Rs.', 'Rs', ',', '%', '(', ')', '"'):
        s = s.replace(ch, '')
    s = s.strip()
    if not s or s in ('-', '--'):
        return default
    try:
        val = float(s)
    except ValueError:
        return default
    return -val if neg else val


# The Pre-Sales Dump, column for column, in the order the ERP writes it.
# (header, is_read) — a False here means the column is allowed to be present
# and is deliberately not stored. Keeping the ignored ones IN the template is
# the point: the export is pasted in whole, and a column listed as ignored is
# a decision somebody can argue with, where a column silently dropped is not.
PRE_SALES_DUMP = [
    ('Customer Type', True), ('Type', True), ('Order Date *', True),
    ('Customer No.', True), ('Customer Name', True), ('Cust.State Code', True),
    ('Customer City', True), ('Customer District', True), ('Key', False),
    ('Zone', True), ('Subzone', True), ('RSM Name', True), ('ASM Name', True),
    ('Invoice Date', True), ('Invoice No.', True), ('Posting Date', True),
    ('External Doc. No.', True), ('Cust. GST Reg.', True), ('Location', True),
    ('Location State', True), ('Location Gst Reg.', False), ('Global Dim1', False),
    ('Packeging Type', True), ('Item Sub Type', True), ('Currency Code', True),
    ('Gen. Bus. Posting Group', True), ('Item Code', True), ('Item Name', True),
    ('Pack Size', True), ('Batch No.', True), ('PKD', True), ('Use By', True),
    ('Quantity', True), ('Unit Of Measure Code', True), ('HSN Code', True),
    ('Unit Price', True),
    ('Invoice Disc.%', False), ('Invoice Disc.Amt', True),
    ('Retail Scheme %', False), ('Retail Scheme Amt', True),
    ('Wholesale Scheme %', False), ('Wholesale Scheme Qty Slab', False),
    ('Wholesale Scheme Amt', True),
    ('Taxable Amount *', True), ('Value in Lakhs', False),
    ('Total Line Amount GST & TCS', True),
    ('TCS Section Code', False), ('TCS Aseessee Code', False), ('TCS %', False),
    ('TCS Amount', True), ('GST Jurisdiction Type', True),
    ('Line Amount(FC)', True), ('Exchange Rate', True),
    ('IGST %', False), ('IGST Amount', True),
    ('CGST %', False), ('CGST Amount', True),
    ('SGST %', False), ('SGST Amount', True),
    ('I-CODE', True), ('Item Category', True),
    ('Item Sub Category (Sub Brand)', True), ('Variant', True), ('Prod. Group', True),
    ('GL Account No.', False), ('GL Account Name', False),
    ('Gross Weight In(Kg)', True), ('Net Weight In(Kg)', True),
    ('Sales Order No.', True), ('MRP', True), ('Cancelled *', True),
    ('Customer Posting Group(Business type)', True),
    ('Customer Price Group(warehousetype)', True),
    ('Reason Code', True), ('V-REMARS', True),
]

# One filled line, so the shape of a row is obvious. Values follow the header
# order above exactly.
_SAMPLE = [
    'Distributor', 'Invoice', '2026-04-05', 'CUST-001', 'Sharma Traders', '07',
    'New Delhi', 'Central Delhi', '', 'North', 'Delhi NCR', 'Anil Mehra',
    'Vikas Gupta', '2026-04-05', 'INV-1001', '2026-04-05', 'PO-9981',
    '07AABCA1234A1Z5', 'Delhi Depot', 'Delhi', '', '', 'Jar', 'Honey', 'INR',
    'DOMESTIC', 'APS-HNY-500', 'APIS Himalaya Honey 500g', '500g', 'B-2604',
    '2026-03-01', '2028-02-29', 120, 'PCS', '04090000', 250,
    5, 1500, 2, 600, 1, '', 300, 27600, 0.28, 30084,
    '', '', 0, 0, 'Intra-State', 0, 0,
    0, 0, 9, 1242, 9, 1242,
    'IC-4412', 'Honey', 'Natural Honey', 'Squeeze', 'Honey Group',
    '', '', 62.5, 60.0, 'SO-3321', 280, 'No',
    'Domestic Customer', 'Depot', '', '',
]


# Headers the Pre-Sales Dump carries that we choose not to store. Held here
# so the upload can tell the operator "known, skipped on purpose" instead of
# "not recognised" — the second phrasing sends somebody hunting for a mapping
# bug that does not exist, on every single upload.
IGNORED_HEADERS = {_norm(h) for h, used in PRE_SALES_DUMP if not used}


def partition_unknown(unknown):
    """-> (deliberately skipped, genuinely unrecognised)."""
    skipped, unrecognised = [], []
    for h in unknown:
        (skipped if _norm(h) in IGNORED_HEADERS else unrecognised).append(h)
    return skipped, unrecognised


def build_template():
    """The Pre-Sales Dump template, plus a sheet explaining what is read.

    The upload reads headers by alias rather than by position, so a raw ERP
    export can be dropped in as-is and this template is really documentation
    of what SalesIQ does with each column.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Pre Sales Dump'

    READ = '1F4E79'        # stored
    KEY = 'C00000'         # stored and load-bearing (marked *)
    IGNORED = '808080'     # allowed through, deliberately not stored

    border = Border(*(Side(style='thin'),) * 4)
    for ci, (h, used) in enumerate(PRE_SALES_DUMP, 1):
        colour = (KEY if '*' in h else READ) if used else IGNORED
        c = ws.cell(row=1, column=ci, value=h)
        c.fill = PatternFill(start_color=colour, end_color=colour, fill_type='solid')
        c.font = Font(color='FFFFFF', bold=True, size=9)
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        c.border = border
        ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = \
            max(12, min(len(h) + 2, 26))

    for ci, v in enumerate(_SAMPLE, 1):
        ws.cell(row=2, column=ci, value=v).border = border

    ws.row_dimensions[1].height = 46
    ws.freeze_panes = 'D2'

    # Text on the two columns Excel likes to reinterpret. A batch number such
    # as 042026 loses its leading zero as a number, and a date typed as
    # "Apr-26" stops being text at all.
    for header in ('Batch No.', 'Cancelled *'):
        names = [h for h, _ in PRE_SALES_DUMP]
        if header in names:
            letter = openpyxl.utils.get_column_letter(names.index(header) + 1)
            for r in range(2, 5002):
                ws[f'{letter}{r}'].number_format = '@'

    # ── Reference sheet ──
    ref = wb.create_sheet('How To Use')
    ref.column_dimensions['A'].width = 30
    ref.column_dimensions['B'].width = 96
    rows = [
        ('SalesIQ — Primary Sales Upload', ''),
        ('', ''),
        ('Drop the export in as-is',
         'Headers are matched by name, not by position. Export the Pre-Sales Dump from '
         'the ERP and upload it — columns may be reordered, renamed slightly, or absent.'),
        ('Blue columns', 'Read and stored.'),
        ('Red columns (*)',
         'Load-bearing. Order Date decides which month a sale lands in, Taxable Amount is '
         'the sales value every figure is built on, and Cancelled decides whether the row '
         'counts at all.'),
        ('Grey columns',
         'Allowed to be present and deliberately not stored. Every percentage column '
         '(Invoice Disc.%, Retail Scheme %, IGST %, TCS %) is derivable from the amount '
         'beside it, and a stored percentage that disagrees with its own amount is a '
         'number nobody can act on. Value in Lakhs is Taxable Amount restated. Key, '
         'Global Dim1, GL Account and the TCS code columns are ledger plumbing, not sales.'),
        ('Cancelled',
         'Loaded, then excluded from every sales figure — so the file still reconciles '
         'against the ERP while no cancelled invoice is ever reported as revenue. '
         'Anything other than Yes / Y / True / 1 is read as NOT cancelled, on purpose: '
         'an unrecognised value must not accidentally hide a real sale.'),
        ('Type / credit memos',
         'A Credit Memo is flagged as a return. Amounts are stored exactly as the file '
         'gives them. If your export writes returns as POSITIVE numbers, say so — they '
         'would otherwise add to sales instead of reducing them.'),
        ('Taxable Amount vs Total Line Amount',
         'Taxable Amount (after scheme and discount, before GST) is what the dashboards '
         'report. Total Line Amount GST & TCS is stored alongside it for reconciliation.'),
        ('Discount and tax',
         'Summed from their parts — Invoice Disc.Amt + Retail Scheme Amt + Wholesale '
         'Scheme Amt, and IGST + CGST + SGST — rather than asking for a pre-totalled '
         'column that would then disagree with the parts beside it.'),
        ('Dates',
         'Any common format works: 2026-04-05, 05-04-2026, 5 Apr 2026. Day-first is '
         'assumed for ambiguous dates, so 05-04-2026 is 5 April. PKD and Use By are '
         'read the same way, so shelf-life reporting works off the same upload.'),
        ('Weights',
         'Gross and Net Weight In(Kg) are stored, so volume can be reported in tonnes '
         'as well as in rupees.'),
        ('Unrecognised columns',
         'Reported back after upload rather than dropped silently. If something you '
         'need shows up in that list, tell us and it gets mapped.'),
        ('Multiple uploads',
         'Uploads add to the dataset, and any single upload can be removed again from '
         'the dashboard if the wrong file goes in.'),
    ]
    for ri, (a, b) in enumerate(rows, 1):
        ref.cell(row=ri, column=1, value=a).font = Font(bold=True, size=12 if ri == 1 else 10)
        c = ref.cell(row=ri, column=2, value=b)
        c.alignment = Alignment(wrap_text=True, vertical='top')

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
