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
    ('Work Location',  'work_location',  20, False,
     'State, picked from the list - or a city such as Delhi HO, Roorkee, Mumbai.\n\n'
     'Sets the minimum wage Basic is checked against.'),
    ('CTC',            'ctc',            16, True,
     'Total annual CTC in rupees, including Variable Pay and Gift. Required.\n\n'
     'e.g. 1200000 (commas and the ₹ sign are fine too).'),
]

# Dropdowns on the template.
CHOICES = {
    'category': ['Sales', 'HO', 'Plant'],
    'cadre': ['D', 'C', 'M', 'O', 'W'],
}

MAX_LENGTH = {
    'candidate_name': 200, 'category': 20, 'department': 120, 'designation': 120,
    'grade': 40, 'cadre': 60, 'work_location': 120,
}

# Largest annual CTC accepted — guards against a value typed in paise or
# with a stray extra digit; fits the model's 12-digit field with room.
MAX_CTC = 100_000_000
