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
        'mode_options': mode_options(level),
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


# Canonical travel modes offered on the sanction form. Lives here (not just in
# the UI) so entitlement is decided against the same list the form renders.
TRAVEL_MODES = [
    'Train', 'Flight', 'Bus', 'Cab / Taxi', 'Own Car',
    'Own Two-Wheeler', 'Auto Rickshaw', 'Company Vehicle',
]


def mode_options(level):
    """Split the travel modes into what this level is entitled to and what needs
    an exception. The form shows the entitled set first; picking anything from
    the second list is allowed but must carry a reason for the approver."""
    entitled, exception = [], []
    for m in TRAVEL_MODES:
        within, _, flags = mode_entitlement(level, m)
        (entitled if within else exception).append({
            'mode': m, 'note': flags[0] if flags else '',
        })
    band = band_for_level(level)
    return {
        'entitled': entitled, 'exception': exception,
        'entitled_mode': DA_MATRIX.get(band, {}).get('mode'),
    }


def trip_days(from_date, to_date):
    """Inclusive day count for a trip. None if either date is missing/invalid."""
    if not from_date or not to_date:
        return None
    days = (to_date - from_date).days + 1
    return days if days > 0 else None


def mode_entitlement(level, mode):
    """Check a chosen travel mode against the band's approved class.

    Returns (within_entitlement, entitled_mode, flags[]). Nothing is hard-blocked
    here — a sanction is a *request*, and genuine exceptions (no train available,
    urgent travel) are the approver's call. Flags travel with the request so the
    manager sees exactly what is out of policy.
    """
    band = band_for_level(level)
    entitled = DA_MATRIX.get(band, {}).get('mode')
    flags = []
    m = (mode or '').strip().lower()
    if not m or not entitled:
        return True, entitled, flags

    ent = entitled.lower()
    within = True

    if 'flight' in m or 'air' in m:
        # Air is M1+ only, and needs the distance/duration justification at claim time.
        if not air_travel_allowed(level, distance_km=AIR_MIN_DISTANCE_KM):
            within = False
            flags.append(f'Air travel is not in the approved class for this level ({entitled})')
        else:
            flags.append(f'Air travel needs ≥{AIR_MIN_DISTANCE_KM} km or >{AIR_MIN_TRAIN_HOURS} h by train — justify at claim')
    elif 'train' in m:
        pass                      # class (AC tier) is checked on the actual ticket, not here
    elif 'cab' in m or 'taxi' in m:
        if 'taxi' not in ent:
            within = False
            flags.append(f'Taxi is not in the approved class for this level ({entitled})')
    elif 'own car' in m or 'own two' in m or 'two-wheeler' in m:
        letter, n = level_number(level)
        if 'own car' in m and not (letter == 'M' and n >= 1):
            within = False
            flags.append('Own 4-wheeler reimbursement is M1 and above only')
    return within, entitled, flags


def estimate_breakdown(level, city, days, misc=0, ticket=0):
    """Policy-driven cost estimate for a tour sanction.

    Ticket fare is NOT derived — the policy defines an entitled *class*, not
    rupee fares, so the employee supplies the fare and we surface the class they
    are entitled to. Lodging / food / local conveyance all come straight from
    the approved matrices.

    Lodging is charged per NIGHT (days - 1); DA and local conveyance per DAY.
    """
    grade = city_grade(city)
    band = band_for_level(level)
    days = int(days or 0)
    nights = max(0, days - 1)

    stay = stay_cap(level, grade)
    da = da_cap(level, grade)
    local_daily = LOCAL_CONVEYANCE.get(band, {}).get('daily')

    # 'actual' bands (M7+) have no ceiling — nothing to pre-compute.
    stay_rate = None if stay == 'actual' else stay
    da_rate = None if da == 'actual' else da

    lodging_amt = round(stay_rate * nights, 2) if stay_rate is not None else 0.0
    food_amt = round(da_rate * days, 2) if da_rate is not None else 0.0
    local_amt = round(local_daily * days, 2) if local_daily is not None else 0.0
    ticket_amt = float(ticket or 0)
    misc_amt = float(misc or 0)

    return {
        'level': level, 'band': band, 'city': city, 'city_grade': grade,
        'days': days, 'nights': nights,
        'entitled_mode': DA_MATRIX.get(band, {}).get('mode'),
        'rates': {
            'stay_per_night': stay, 'da_per_day': da, 'local_per_day': local_daily,
        },
        'lines': {
            'ticket': ticket_amt, 'lodging': lodging_amt,
            'food': food_amt, 'local': local_amt, 'misc': misc_amt,
        },
        'caps': {
            'lodging': lodging_amt if stay_rate is not None else None,
            'food': food_amt if da_rate is not None else None,
            'local': local_amt if local_daily is not None else None,
        },
        'total': round(ticket_amt + lodging_amt + food_amt + local_amt + misc_amt, 2),
    }


def leg_estimate(level, city, days, nights, ticket=0):
    """Policy amounts for one stop of a tour, costed at that city's own grade."""
    grade = city_grade(city)
    band = band_for_level(level)
    stay, da = stay_cap(level, grade), da_cap(level, grade)
    local_daily = LOCAL_CONVEYANCE.get(band, {}).get('daily')
    stay_rate = None if stay == 'actual' else stay
    da_rate = None if da == 'actual' else da

    lodging = round(stay_rate * nights, 2) if stay_rate is not None else 0.0
    food = round(da_rate * days, 2) if da_rate is not None else 0.0
    local = round(local_daily * days, 2) if local_daily is not None else 0.0
    return {
        'city': city, 'city_grade': grade, 'days': days, 'nights': nights,
        'rates': {'stay_per_night': stay, 'da_per_day': da, 'local_per_day': local_daily},
        'lines': {'ticket': float(ticket or 0), 'lodging': lodging, 'food': food, 'local': local},
        'caps': {'lodging': lodging if stay_rate is not None else None,
                 'food': food if da_rate is not None else None,
                 'local': local if local_daily is not None else None},
        'subtotal': round(float(ticket or 0) + lodging + food + local, 2),
    }


def itinerary_estimate(level, legs, misc=0):
    """Cost a multi-stop tour leg by leg, each at its own city grade.

    Nights are assigned to the city you sleep in: every leg but the last is
    charged for as many nights as it has days (the night after the final day is
    spent travelling on to the next city, or sleeping there), and the last leg
    drops one night because that day ends back at HQ. For legs that tile the
    trip contiguously this sums to exactly (total days - 1) nights, matching how
    a single-destination trip is costed.
    """
    ordered = sorted(
        [l for l in legs if l.get('from_date') and l.get('to_date')],
        key=lambda l: l['from_date'],
    )
    n = len(ordered)
    out, total_days = [], 0
    for i, leg in enumerate(ordered):
        d = trip_days(leg['from_date'], leg['to_date']) or 0
        nights = d if i < n - 1 else max(0, d - 1)
        e = leg_estimate(level, leg.get('destination_city', ''), d, nights,
                         ticket=leg.get('est_ticket_amount', 0))
        # Keep the caller's own id for this leg. Legs are sorted by date to work
        # out nights, so the position here is NOT the position the caller sent —
        # returning the sorted index would hand each leg its neighbour's costs.
        e['seq'] = leg.get('seq', i)
        e['order'] = i
        e['from_date'], e['to_date'] = str(leg['from_date']), str(leg['to_date'])
        e['travel_mode'] = leg.get('travel_mode', '')
        out.append(e)
        total_days += d

    misc_amt = float(misc or 0)

    # Trip ceiling per head = sum of the stops' ceilings. If any stop is an
    # 'actual' band there is no ceiling for that head across the trip either.
    def _cap(head):
        vals = [l['caps'][head] for l in out]
        return None if not vals or any(v is None for v in vals) else round(sum(vals), 2)

    return {
        'legs': out,
        'total_days': total_days,
        'total_nights': sum(l['nights'] for l in out),
        'caps': {'lodging': _cap('lodging'), 'food': _cap('food'), 'local': _cap('local')},
        'lines': {
            'ticket': round(sum(l['lines']['ticket'] for l in out), 2),
            'lodging': round(sum(l['lines']['lodging'] for l in out), 2),
            'food': round(sum(l['lines']['food'] for l in out), 2),
            'local': round(sum(l['lines']['local'] for l in out), 2),
            'misc': misc_amt,
        },
        'total': round(sum(l['subtotal'] for l in out) + misc_amt, 2),
    }


def validate_itinerary(legs, trip_from=None, trip_to=None):
    """Flag overlapping, out-of-window or unallocated days across the legs."""
    flags = []
    ordered = sorted(
        [l for l in legs if l.get('from_date') and l.get('to_date')],
        key=lambda l: l['from_date'],
    )
    for l in ordered:
        if l['to_date'] < l['from_date']:
            flags.append(f"{l.get('destination_city') or 'A stop'} ends before it starts")

    for a, b in zip(ordered, ordered[1:]):
        if b['from_date'] <= a['to_date']:
            flags.append(f"{a.get('destination_city') or 'stop'} and {b.get('destination_city') or 'stop'} overlap")
        elif (b['from_date'] - a['to_date']).days > 1:
            gap = (b['from_date'] - a['to_date']).days - 1
            flags.append(f"{gap} day(s) between {a.get('destination_city') or 'stop'} and "
                         f"{b.get('destination_city') or 'stop'} are not assigned to any city — no DA estimated for them")

    if ordered and trip_from and trip_to:
        if ordered[0]['from_date'] < trip_from or ordered[-1]['to_date'] > trip_to:
            flags.append('Itinerary falls outside the overall travel dates')
        else:
            if (ordered[0]['from_date'] - trip_from).days > 0:
                flags.append(f"First stop starts {(ordered[0]['from_date'] - trip_from).days} day(s) after the trip start date")
            if (trip_to - ordered[-1]['to_date']).days > 0:
                flags.append(f"Last stop ends {(trip_to - ordered[-1]['to_date']).days} day(s) before the trip end date")
    return flags


def validate_estimate(level, city, days, lodging=0, food=0, local=0, advance=0, total=0):
    """Flag an employee-adjusted estimate against policy ceilings."""
    base = estimate_breakdown(level, city, days)
    caps = base['caps']
    flags = []
    for key, label, val in (
        ('lodging', 'Lodging', lodging), ('food', 'Food / DA', food),
        ('local', 'Local conveyance', local),
    ):
        cap = caps.get(key)
        if cap is not None and float(val or 0) > cap:
            flags.append(f'{label} estimate ₹{float(val):,.0f} exceeds policy ceiling ₹{cap:,.0f}')
    if float(advance or 0) > float(total or 0):
        flags.append('Advance requested is more than the total estimated expense')
    return flags


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
