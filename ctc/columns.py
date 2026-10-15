"""The CTC template's columns — the one list both the downloadable template
and the uploader read, so the two cannot drift apart.

(heading in the sheet, model field, width, required, help shown on the cell)
"""
COLUMNS = [
    ('Employee Code',  'employee_code',  16, False,
     'Optional - e.g. SL00225, HO00112, RK00045. Printed on the Annexure A.'),
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
    ('Date of Joining', 'date_of_joining', 16, False,
     'Optional - as a date, or typed day-month-year, e.g. 01-07-2026.\n\n'
     'Printed on the Annexure A.'),
    ('Work Location',  'work_location',  20, False,
     'State, picked from the list - or a city such as Delhi HO, Roorkee, Mumbai.\n\n'
     'Sets the minimum wage Basic is checked against.'),
    ('CTC',            'ctc',            16, True,
     'Total annual CTC in rupees, including Variable Pay and Gift. Required.\n\n'
     'e.g. 1200000 (commas and the ₹ sign are fine too).'),
]

# Gross Earnings: one Yes / No column per item, after CTC, in the order the
# Annexure A lists them. Optional, so a sheet without them still uploads.
# Cadres O and W get none of these, so their cells are greyed out on the
# template and anything typed there is ignored.
# (heading, key, what a blank cell means)
OPTION_COLUMNS = [
    ('Meal Voucher Reimbursement', 'meal', True),
    ('Telephone-Handset / Accessories & Internet Reimbursement', 'telephone', True),
    ('Health & Wellness Reimbursement', 'health', True),
    ('Books, Periodicals & Professional Development Reimbursement', 'books', True),
    ('Uniform & Attire Reimbursement', 'uniform', True),
    ('Fuel & Vehicle Maintenance Reimbursement', 'fuel', True),
    ('Driver/Chauffer Salary', 'driver', True),
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
    'employee_code': 40, 'candidate_name': 200, 'category': 20, 'department': 120, 'designation': 120,
    'grade': 40, 'cadre': 60, 'work_location': 120,
}

# Largest annual CTC accepted — guards against a value typed in paise or
# with a stray extra digit; fits the model's 12-digit field with room.
MAX_CTC = 100_000_000
