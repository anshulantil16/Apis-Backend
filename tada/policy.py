"""
APIS India TA/DA policy engine — allowance matrices, city grading, and validation.
All limits per the approved PMS/TA-DA Policy (FY 2026-27, v1).
"""
from datetime import date

COMPANY_NAME = 'Apis India Limited'
GSTIN = '05AAACM0656K1ZL'
SUBMISSION_DEADLINE_DAYS = 60          # block claims older than 60 days from travel

# ── Daily local conveyance + monthly phone/internet by level band (₹) ─────────
LOCAL_CONVEYANCE = {
    'M5-M6': {'daily': 400, 'phone': 500},
    'M3-M4': {'daily': 375, 'phone': 500},
    'M1-M2': {'daily': 325, 'phone': 500},
    'E3-E4': {'daily': 250, 'phone': 500},
    'E1-E2': {'daily': 225, 'phone': 500},
}

# ── Ex-HQ night-stay + DA by band × city grade + approved mode ────────────────
# Values: (max_stay_per_night, daily_allowance). 'actual' = as per actuals.
DA_MATRIX = {
    'M7+':   {'A': ('actual', 'actual'), 'B': ('actual', 'actual'), 'C': ('actual', 'actual'),
              'mode': '1st AC Train / Air / Taxi'},
    'M5-M6': {'A': (2800, 600), 'B': (2200, 475), 'C': (1850, 400), 'mode': '2nd AC Train / Air / Taxi'},
    'M3-M4': {'A': (2500, 575), 'B': (2100, 450), 'C': (1700, 375), 'mode': '2nd AC Train / Air / Taxi'},
    'M1-M2': {'A': (1800, 475), 'B': (1450, 325), 'C': (1250, 275), 'mode': '3rd AC Train / AC Bus / Taxi'},
    'E3-E4': {'A': (1500, 325), 'B': (1250, 275), 'C': (950, 225),  'mode': '2nd Sleeper / Ordinary Bus'},
    'E1-E2': {'A': (1350, 300), 'B': (1100, 250), 'C': (850, 200),  'mode': '2nd Sleeper / Ordinary Bus'},
}

# ── City grading ──────────────────────────────────────────────────────────────
CITY_GRADE_A = [
    'delhi', 'new delhi', 'ncr', 'gurgaon', 'gurugram', 'noida', 'ghaziabad', 'faridabad',
    'mumbai', 'navi mumbai', 'thane', 'goa', 'panaji',
    'shimla', 'manali', 'nainital', 'mussoorie', 'darjeeling', 'gangtok', 'srinagar', 'ooty',
]
CITY_GRADE_B = [
    'pune', 'ahmedabad', 'bengaluru', 'bangalore', 'chennai', 'kolkata', 'hyderabad',
    'jaipur', 'lucknow', 'chandigarh', 'bhopal', 'patna', 'raipur', 'ranchi', 'bhubaneswar',
    'thiruvananthapuram', 'dehradun', 'gandhinagar', 'shillong', 'guwahati', 'surat',
    'nagpur', 'indore', 'kochi', 'coimbatore', 'visakhapatnam', 'vadodara',
]

# ── Vehicle & air rules ───────────────────────────────────────────────────────
VEHICLE_RATE_4W = 10.0          # ₹/km, M1 and above only
VEHICLE_RATE_2W = 4.0           # ₹/km
FOUR_WHEELER_DEDUCT_KM = 35     # first 35 km of daily HQ-local deducted for 4-wheelers
FOUR_WHEELER_MAX_KM = 300       # per day
AIR_MIN_DISTANCE_KM = 600
AIR_MIN_TRAIN_HOURS = 10


def band_for_level(level):
    """Map a level like 'M5', 'E3', 'M7' to a policy band key."""
    lv = (level or '').strip().upper()
    if not lv:
        return None
    letter = lv[0]
    digits = ''.join(c for c in lv if c.isdigit())
    n = int(digits) if digits else 0
    if letter == 'M':
        if n >= 7: return 'M7+'
        if n >= 5: return 'M5-M6'
        if n >= 3: return 'M3-M4'
        if n >= 1: return 'M1-M2'
    if letter == 'E':
        if n >= 3: return 'E3-E4'
        if n >= 1: return 'E1-E2'
    return None


def level_number(level):
    lv = (level or '').strip().upper()
    digits = ''.join(c for c in lv if c.isdigit())
    return (lv[:1], int(digits) if digits else 0)


def city_grade(city):
    """Return 'A' / 'B' / 'C' for a destination city."""
    c = (city or '').strip().lower()
    if not c:
        return 'C'
    if any(x in c for x in CITY_GRADE_A):
        return 'A'
    if any(x in c for x in CITY_GRADE_B):
        return 'B'
    return 'C'


def get_caps(level):
    """Full policy caps for a level — used by the UI to show limits & validate."""
    band = band_for_level(level)
    da = DA_MATRIX.get(band, {})
    return {
        'level': level,
        'band': band,
        'local_conveyance_daily': LOCAL_CONVEYANCE.get(band, {}).get('daily'),
        'phone_monthly': LOCAL_CONVEYANCE.get(band, {}).get('phone'),
        'approved_travel_mode': da.get('mode'),
        'da_matrix': {g: da.get(g) for g in ('A', 'B', 'C')} if da else {},
        'gstin': GSTIN,
        'company': COMPANY_NAME,
        'submission_deadline_days': SUBMISSION_DEADLINE_DAYS,
    }


def stay_cap(level, grade):
    band = band_for_level(level)
    row = DA_MATRIX.get(band)
    if not row or grade not in row:
        return None
    v = row[grade]
    return v[0] if isinstance(v, (list, tuple)) else v


def da_cap(level, grade):
    band = band_for_level(level)
    row = DA_MATRIX.get(band)
    if not row or grade not in row:
        return None
    v = row[grade]
    return v[1] if isinstance(v, (list, tuple)) else v


def is_within_deadline(travel_date):
    """False if the travel date is more than 60 days ago (hard-stop)."""
    if not travel_date:
        return True
    return (date.today() - travel_date).days <= SUBMISSION_DEADLINE_DAYS


def air_travel_allowed(level, distance_km=0, train_hours=0):
    """Air travel: M1+ only, and distance >= 600km OR train time > 10h."""
    letter, n = level_number(level)
    if letter != 'M':
        return False
    dist_ok = (distance_km or 0) >= AIR_MIN_DISTANCE_KM or (train_hours or 0) > AIR_MIN_TRAIN_HOURS
    return dist_ok


def vehicle_amount(mode, km):
    """Reimbursement for own-vehicle travel. 4-wheeler deducts first 35km & caps 300km/day."""
    km = float(km or 0)
    m = (mode or '').lower()
    if 'four' in m or '4' in m or 'car' in m:
        billable = max(0.0, min(km, FOUR_WHEELER_MAX_KM) - FOUR_WHEELER_DEDUCT_KM)
        return round(billable * VEHICLE_RATE_4W, 2)
    if 'two' in m or '2' in m or 'bike' in m or 'scooter' in m:
        return round(km * VEHICLE_RATE_2W, 2)
    return 0.0


def validate_expense_item(user_level, category, city_grade_val, claimed, has_bill, mode='', km=0, date_val=None):
    """Return (approved_cap, flags[]) for an expense line against policy."""
    flags = []
    claimed = float(claimed or 0)
    cap = None

    if not is_within_deadline(date_val):
        flags.append('Beyond 60-day submission deadline')

    if category == 'lodging':
        cap = stay_cap(user_level, city_grade_val)
        if cap == 'actual':
            cap = None
        if not has_bill:
            flags.append('No hotel invoice → defaults to "own arrangement" (DA only, stay not paid)')
            cap = 0
        elif cap is not None and claimed > cap:
            flags.append(f'Exceeds stay cap ₹{cap} for grade-{city_grade_val} city')
    elif category == 'food':
        cap = da_cap(user_level, city_grade_val)
        if cap == 'actual':
            cap = None
        if cap is not None and claimed > cap:
            flags.append(f'Exceeds DA cap ₹{cap}/day for grade-{city_grade_val} city')
    elif category == 'local_transport':
        band = band_for_level(user_level)
        dc = LOCAL_CONVEYANCE.get(band, {}).get('daily')
        cap = dc
        if 'wheeler' in (mode or '').lower() or 'car' in (mode or '').lower() or 'bike' in (mode or '').lower():
            veh = vehicle_amount(mode, km)
            cap = veh
        elif dc is not None and claimed > dc:
            flags.append(f'Exceeds daily local conveyance cap ₹{dc}')

    if category in ('travel', 'lodging', 'misc') and not has_bill:
        flags.append('Tax invoice mandatory — no bill, no approval')

    return cap, flags
