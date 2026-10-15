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

BASIC_PERCENT = 50                       # of Gross Salary, or the state minimum wage if higher
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
# Sales: by grade; department (None = either) settles grades with two rates.
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
# Everyone outside Sales (HO and Plant), whatever their grade.
VPP_NON_SALES = 8

# Minimum wages per month, by state (P & C's table). Basic is never below
# the minimum wage of the candidate's work location.
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
