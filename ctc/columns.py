"""The CTC template's columns — the one list both the downloadable template
and the uploader read, so the two cannot drift apart.

(heading in the sheet, model field, width, required, help shown on the cell)
"""
COLUMNS = [
    ('Candidate Name', 'candidate_name', 28, True,  'Full name of the candidate. Required.'),
    ('Category',       'category',       12, True,
     'Sales, HO or Plant. Required.\n\nDecides which Variable Pay table applies.'),
    ('Department',     'department',     24, False,
     'e.g. General Trade, Alternate Channels, Finance.\n\nFor Sales, used with the '
     'designation to find the Variable Pay %.'),
    ('Designation',    'designation',    30, False,
     'Job title being offered, e.g. Area Sales Manager.'),
    ('Grade',          'grade',          10, True,  'e.g. M1, M3, O5, C2. Required.'),
    ('Cadre',          'cadre',          10, True,  'D, C, M, O or W. Required.'),
    ('Number of Children', 'number_of_children', 12, False,
     'Optional - 0, 1, 2 ... Leave blank for none.\n\nChild Education Allowance is '
     'Rs 3,000 a month per child, for up to 2 children (cadres D, C, M).'),
    ('Work Location',  'work_location',  20, False,
     'State, picked from the list - or a city such as Delhi HO, Roorkee, Mumbai.\n\n'
     'Sets the minimum wage the CTC per month is checked against.'),
    ('CTC',            'ctc',            16, True,
     'Total annual CTC in rupees, including Variable Pay and Gift. Required.\n\n'
     'e.g. 1200000 (commas and the ₹ sign are fine too).'),
    ('Tax Regime',     'tax_regime',     16, False,
     'Old Regime, New Regime or Both Regime, picked from the list.\n\n'
     'Only for a CTC above Rs 12 lakh - greyed out otherwise. New Regime leaves out '
     'Books and Uniform (Old Regime only).'),
]

# Columns a sheet may leave out altogether (added after the template first
# went out, so an older filled-in template still uploads).
OPTIONAL_FIELDS = {'tax_regime'}

TAX_REGIMES = ['Old Regime', 'New Regime', 'Both Regime']

# Gross Earnings: one Yes / No column per item, after CTC, in the Reference
# Master's preference order. Optional, so a sheet without them still uploads.
# Cadres O and W, and any CTC up to Rs 12 lakh, get none of these, so their
# cells are greyed out on the template and anything typed there is ignored.
# (heading, key, what a blank cell means) - blank is No: only what is picked is paid
OPTION_COLUMNS = [
    ('Leave Travel Allowance', 'lta', False),
    ('Meal Voucher Reimbursement', 'meal', False),
    ('Telephone-Handset / Accessories & Internet Reimbursement', 'telephone', False),
    ('Fuel & Vehicle Maintenance Reimbursement', 'fuel', False),
    ('Driver/Chauffer Salary', 'driver', False),
    ('Health & Wellness Reimbursement', 'health', False),
    ('Books, Periodicals & Professional Development Reimbursement', 'books', False),
    ('Uniform & Attire Reimbursement', 'uniform', False),
    ('Car Lease / Company Car Benefit', 'car', False),
]
OPTION_CADRES = {'D', 'C', 'M'}          # the cadres these apply to
YES = {'yes', 'y', 'true', '1'}
NO = {'no', 'n', 'false', '0'}
NOT_ANSWERED = {'', 'na', 'n/a', '-', 'not applicable'}

# Dropdowns on the template.
CHOICES = {
    'category': ['Sales', 'HO', 'Plant'],
    'cadre': ['D', 'C', 'M', 'O', 'W'],
}

MAX_LENGTH = {
    'candidate_name': 200, 'category': 20, 'department': 120, 'designation': 120,
    'grade': 40, 'cadre': 60, 'work_location': 120, 'tax_regime': 20,
}

# Largest annual CTC accepted — guards against a value typed in paise or
# with a stray extra digit; fits the model's 12-digit field with room.
MAX_CTC = 100_000_000
