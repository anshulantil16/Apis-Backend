"""APIS compensation rules — Salary Fixation Model 2026.

Transcribed from P & C's "COMPENSATION STRUCTURE_Approved _ V1_23 July 2026"
workbook (sheets "CTC MASTER Approved" and "CTC & Reimb Slab WI _ Approved"),
plus the VPP and Mediclaim tables P & C supplied separately. All amounts are
monthly rupees unless named otherwise.

Values the workbook leaves open — LTA, Gratuity, the D cadre's "Actual"
reimbursements — are not here: they are CtcSettings rows a super admin
edits, and start at 0.

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

BASIC_PERCENT = 50                       # of Gross Earnings
HRA_PERCENT = {'D': 50, 'C': 50, 'M': 50, 'O': 50, 'W': 20}   # of Basic

PF_PERCENT = 12                          # employer, of Basic up to the PF wage ceiling
                                         # (Rs 25,000 -> PF never above Rs 3,000)
ESI_PERCENT = 3.25                       # employer, of gross salary — small salaries only
BONUS_PERCENT = 8.33                     # statutory, of Basic — small salaries only

GIFT_MONTHLY = {'D': 1250, 'C': 1250, 'M': 1250, 'O': 0, 'W': 0}   # Rs 15,000 a year, paid quarterly

# Children Education Allowance — part of gross earnings. Preference index 1.
# Per child, for at most two children: Rs 6,000 a month is the two-child cap.
CEA_PER_CHILD = {'D': 3000, 'C': 3000, 'M': 3000, 'O': 0, 'W': 0}
CEA_MAX_CHILDREN = 2

# Reimbursements, in the workbook's preference order (index 2..9, gift 7
# is paid separately). When a CTC is too small to carry all of them they
# are dropped from the END of this list first — the workbook says each "can
# be swapped either as Spl Allowance or Reimbursement".
# None = "Actual" in the workbook → the D-cadre amount comes from settings.
# P & C's Reference Master - Salary Fixation Model 2026 (monthly), in its
# preference order. Only what is picked Yes on the template is paid, and an
# item is paid only if it fits in full in the Special Allowance still left.
# (key, label, {cadre: monthly}, regime) - 'old': Old Regime only.
REIMBURSEMENTS = [
    ('lta',       'Leave Travel Allowance (LTA)',
     {'D': 25000, 'C': 15000, 'M': 5000, 'O': 0, 'W': 0}, 'both'),
    ('meal',      'Meal Voucher Reimbursement',
     {'D': 10400, 'C': 9600, 'M': 5200, 'O': 0, 'W': 0}, 'both'),
    ('telephone', 'Telephone-Handset / Accessories & Internet Reimbursement',
     {'D': 10000, 'C': 5000, 'M': 2500, 'O': 0, 'W': 0}, 'both'),
    ('fuel',      'Fuel & Vehicle Maintenance Reimbursement',
     {'D': 5000, 'C': 5000, 'M': 5000, 'O': 0, 'W': 0}, 'both'),
    ('driver',    'Driver / Chauffeur Salary',
     {'D': 3000, 'C': 3000, 'M': 3000, 'O': 0, 'W': 0}, 'both'),
    ('health',    'Health & Wellness Reimbursement',
     {'D': 6000, 'C': 3000, 'M': 1500, 'O': 0, 'W': 0}, 'both'),
    ('books',     'Books, Periodicals & Professional Development Reimbursement',
     {'D': 10000, 'C': 5000, 'M': 2500, 'O': 0, 'W': 0}, 'old'),
    ('uniform',   'Uniform & Attire Reimbursement',
     {'D': 10000, 'C': 5000, 'M': 2500, 'O': 0, 'W': 0}, 'old'),
]

# Reimbursements, LTA and the tax regime apply only above this annual CTC.
REIMBURSEMENT_MIN_CTC = 1_200_000

# Employer mediclaim charge by grade (monthly).
MEDICLAIM = {
    'D': 1150, 'D1': 1150,
    'C5': 1100, 'C4': 1050, 'C3': 1000, 'C2': 950, 'C1': 900,
    'M6': 850, 'M5': 800, 'M4': 750, 'M3': 700, 'M2': 650, 'M1': 600,
    'O5': 550, 'O4': 500, 'O3': 450, 'O2': 400, 'O1': 350,
    'W4': 300, 'W3': 250, 'W2': 200, 'W1': 150,
}

# Variable / Performance Pay, as a % of total CTC - Variable Pay Policy
# Ver. 1.0.0 2024. Managerial roles and above (cadres D, C, M).
#
# Sales: by designation. Each entry's words are matched as whole words in
# the Designation ("ASM", "Area Sales Manager", "Sr. ASM" ...).
VPP_SALES_BY_DESIGNATION = [
    (15, 'Business Head / OT Head', ['business head', 'ot head', 'bh', 'nsh', 'national sales head']),
    (12, 'ZSM / RSM / KAM', ['zsm', 'zonal sales manager', 'rsm', 'regional sales manager',
                             'kam', 'key account manager']),
    (10, 'ASM', ['asm', 'area sales manager']),
]
# "FY 24-25 all existing ASM has been excluded ... ASM joining after
# 01-06-24 will be part of variable pay."
VPP_ASM_JOINED_FROM = (2024, 6, 1)
# Sales from grade M5 up (M5, M6, C1-C5, D): 15%, whatever the designation.
VPP_SALES_SENIOR = 15
VPP_SALES_SENIOR_FROM_GRADE = 'M5'

# Corporate (HO) and Factory (Plant): 8%, M3 grade and above, except Deputy
# and Assistant Managers.
VPP_NON_SALES = 8
VPP_NON_SALES_FROM_GRADE = 'M3'
VPP_NON_SALES_EXCLUDED = ['deputy manager', 'assistant manager']

# Sales designations the policy doesn't name: P & C's grade table.
# Grade; department (None = either) settles grades with two rates.
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

# Minimum wages per month, by state (P & C's table). The CTC per month is
# never below the minimum wage of the candidate's work location.
MIN_WAGES = {
    'Uttar Pradesh': 15500, 'Chhattisgarh': 12700, 'Maharashtra': 15500, 'Telangana': 15000,
    'Karnataka': 18600, 'West Bengal': 13000, 'Odisha': 15000, 'Madhya Pradesh': 15500,
    'Tamil Nadu': 15000, 'Goa': 17500, 'Kerala': 16200, 'Bihar': 14500, 'Delhi': 22500,
    'Himachal Pradesh': 14000, 'Haryana': 18600, 'Andhra Pradesh': 18100, 'Punjab': 14000,
    'Gujarat': 14500, 'Assam': 15500, 'Chandigarh': 15500, 'Rajasthan': 8950,
    'Jammu & Kashmir': 12600, 'Jharkhand': 18100, 'Uttarakhand': 15000, 'Tripura': 10000,
    'Daman (UT)': 13200,
}

# Cities and sites people type in Work Location, and the state they are in.
LOCATION_ALIASES = {
    'newdelhi': 'Delhi', 'delhiho': 'Delhi', 'ho': 'Delhi', 'headoffice': 'Delhi',
    'noida': 'Uttar Pradesh', 'greaternoida': 'Uttar Pradesh', 'ghaziabad': 'Uttar Pradesh',
    'lucknow': 'Uttar Pradesh', 'kanpur': 'Uttar Pradesh', 'prayagraj': 'Uttar Pradesh',
    'pravagraj': 'Uttar Pradesh', 'allahabad': 'Uttar Pradesh', 'varanasi': 'Uttar Pradesh',
    'up': 'Uttar Pradesh',
    'roorkee': 'Uttarakhand', 'haridwar': 'Uttarakhand', 'dehradun': 'Uttarakhand',
    'mumbai': 'Maharashtra', 'pune': 'Maharashtra', 'nagpur': 'Maharashtra',
    'kolkata': 'West Bengal', 'chennai': 'Tamil Nadu', 'coimbatore': 'Tamil Nadu',
    'hyderabad': 'Telangana', 'bangalore': 'Karnataka', 'bengaluru': 'Karnataka',
    'gurgaon': 'Haryana', 'gurugram': 'Haryana', 'faridabad': 'Haryana',
    'vizag': 'Andhra Pradesh', 'visakhapatnam': 'Andhra Pradesh', 'vijayawada': 'Andhra Pradesh',
    'patna': 'Bihar', 'bhilai': 'Chhattisgarh', 'raipur': 'Chhattisgarh',
    'jaipur': 'Rajasthan', 'ahmedabad': 'Gujarat', 'surat': 'Gujarat',
    'kochi': 'Kerala', 'cochin': 'Kerala', 'bhubaneswar': 'Odisha', 'guwahati': 'Assam',
    'ranchi': 'Jharkhand', 'jammu': 'Jammu & Kashmir', 'srinagar': 'Jammu & Kashmir',
    'jk': 'Jammu & Kashmir', 'shimla': 'Himachal Pradesh', 'indore': 'Madhya Pradesh',
    'bhopal': 'Madhya Pradesh', 'ludhiana': 'Punjab', 'amritsar': 'Punjab',
    'panaji': 'Goa', 'agartala': 'Tripura', 'daman': 'Daman (UT)',
}
