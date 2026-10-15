"""APIS compensation rules — Salary Fixation Model 2026.

Transcribed from P & C's "COMPENSATION STRUCTURE_Approved _ V1_23 July 2026"
workbook (sheets "CTC MASTER Approved" and "CTC & Reimb Slab WI _ Approved"),
plus the VPP and Mediclaim tables P & C supplied separately. All amounts are
monthly rupees unless named otherwise.

Values the workbook leaves open — LTA, Gratuity, the D cadre's "Actual"
reimbursements, the W cadre's minimum wage — are not here: they are
CtcSettings rows a super admin edits, and start at 0.

When P & C revises the structure, this is the file to change.
"""

CADRES = {
    'D': 'Director Band',
    'C': 'CXO / HOD / Leadership',
    'M': 'Middle Management Band',
    'O': 'Officer / Supervisor Band',
    'W': 'Workforce / Associate Band',
}

CATEGORIES = ['Sales', 'HO', 'Plant']

BASIC_PERCENT = 50                       # of Gross Salary; W: minimum wage if higher
HRA_PERCENT = {'D': 50, 'C': 50, 'M': 50, 'O': 50, 'W': 20}   # of Basic

PF_PERCENT = 12                          # employer, of Basic up to the PF wage ceiling
ESI_PERCENT = 3.25                       # employer, of gross salary — small salaries only
BONUS_PERCENT = 8.33                     # statutory, of Basic — small salaries only

GIFT_MONTHLY = {'D': 1250, 'C': 1250, 'M': 1250, 'O': 0, 'W': 0}   # Rs 15,000 a year, paid quarterly

# Children Education Allowance — part of gross earnings. Preference index 1.
CEA_MONTHLY = {'D': 6000, 'C': 6000, 'M': 6000, 'O': 0, 'W': 0}

# Reimbursements, in the workbook's preference order (index 2..9, gift 7
# is paid separately). When a CTC is too small to carry all of them they
# are dropped from the END of this list first — the workbook says each "can
# be swapped either as Spl Allowance or Reimbursement".
# None = "Actual" in the workbook → the D-cadre amount comes from settings.
REIMBURSEMENTS = [
    ('meal',      'Meal Voucher Reimbursement',
     {'D': 9600, 'C': 9600, 'M': 5200, 'O': 0, 'W': 0}),
    ('telephone', 'Telephone-Handset / Accessories & Internet Reimbursement',
     {'D': 20000, 'C': 10000, 'M': 5000, 'O': 0, 'W': 0}),
    ('fuel',      'Fuel & Vehicle Maintenance Reimbursement',
     {'D': None, 'C': 5000, 'M': 5000, 'O': 0, 'W': 0}),
    ('driver',    'Driver / Chauffeur Salary',
     {'D': None, 'C': 3000, 'M': 3000, 'O': 0, 'W': 0}),
    ('health',    'Health & Wellness Reimbursement',
     {'D': None, 'C': 6000, 'M': 3000, 'O': 0, 'W': 0}),
    ('books',     'Books, Periodicals & Professional Development Reimbursement',
     {'D': 10000, 'C': 5000, 'M': 2500, 'O': 0, 'W': 0}),
    ('uniform',   'Uniform & Attire Reimbursement',
     {'D': 20000, 'C': 10000, 'M': 5000, 'O': 0, 'W': 0}),
]

# Employer mediclaim charge by grade (monthly).
MEDICLAIM = {
    'D': 1150, 'D1': 1150,
    'C5': 1100, 'C4': 1050, 'C3': 1000, 'C2': 950, 'C1': 900,
    'M6': 850, 'M5': 800, 'M4': 750, 'M3': 700, 'M2': 650, 'M1': 600,
    'O5': 550, 'O4': 500, 'O3': 450, 'O2': 400, 'O1': 350,
    'W4': 300, 'W3': 250, 'W2': 200, 'W1': 150,
}

# Variable / Performance Pay, as a % of total CTC.
# Sales: by grade + designation, within a department (None = either).
VPP_SALES = [
    # grade, department,            designation,                        %
    ('C2', 'General Trade',       'NSH',                               15),
    ('M1', 'General Trade',       'Area Sales Manager',                10),
    ('M1', 'Alternate Channels',  'Key Account Manager',               12),
    ('M2', 'General Trade',       'Senior Area Sales Manager',         10),
    ('M3', 'General Trade',       'Deputy Regional Sales Manager',     10),
    ('M3', 'Alternate Channels',  'Customer Development Manager',      12),
    ('M4', None,                  'Regional Sales Manager',            12),
    ('M5', 'Alternate Channels',  'Assistant General Manager',         15),
]
# HO and Plant: by grade alone, any department or designation.
VPP_BY_GRADE = {
    'HO':    ({'C1', 'C2', 'M3', 'M4', 'M5'}, 8),
    'Plant': ({'M3', 'M4', 'M5'}, 8),
}
