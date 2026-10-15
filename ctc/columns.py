"""The CTC template's columns — the one list both the downloadable template
and the uploader read, so the two cannot drift apart.

(heading in the sheet, model field, width, required, help shown on the cell)
"""
COLUMNS = [
    ('Candidate Name', 'candidate_name', 28, True,  'Full name of the candidate. Required.'),
    ('Department',     'department',     24, False, 'e.g. Sales, Marketing, P&C Admin & IT.'),
    ('Designation',    'designation',    26, False, 'Job title being offered.'),
    ('Grade',          'grade',          12, False, 'e.g. M1, M3, O5.'),
    ('Cadre',          'cadre',          16, False, 'e.g. Management, Officer, Staff.'),
    ('Work Location',  'work_location',  20, False, 'City or site, e.g. Delhi HO, Roorkee.'),
    ('CTC',            'ctc',            16, True,
     'Annual CTC in rupees, as a plain number. Required.\n\ne.g. 1200000 '
     '(commas and the ₹ sign are fine too).'),
]

MAX_LENGTH = {
    'candidate_name': 200, 'department': 120, 'designation': 120,
    'grade': 40, 'cadre': 60, 'work_location': 120,
}

# Largest annual CTC accepted — guards against a value typed in paise or
# with a stray extra digit; fits the model's 12-digit field with room.
MAX_CTC = 100_000_000
