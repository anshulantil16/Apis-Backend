"""Work out a candidate's compensation break-up from their total CTC,
the way P & C's sheet does it (monthly figures):

    CTC               = annual CTC / 12
    Gross Salary      = CTC - VPP - Gift - (Mediclaim or ESI) - Employer PF
                            (- Gratuity, if a rate is set)
    Basic             = 50% of CTC
    HRA               = 50% of Basic          (W: 20%)
    CEA               = Rs 3,000 per child, up to 2 children (D, C, M)
    LTA, reimbursements = picked Yes, at the cadre slab, if they fit
    Special Allowance = Gross Salary - Basic - HRA - CEA - LTA - reimbursements

    Gross Salary up to Rs 21,000: employer ESI (3.25% of Gross Salary) is
        taken instead of Mediclaim; above it, Mediclaim by grade.
    Gross Salary under Rs 25,000: Employer PF = 12% of (Gross Salary - HRA);
        from Rs 25,000: Rs 3,000.

Gross Salary depends on ESI and PF, which depend on Gross Salary, so each
of the four cases (ESI or Mediclaim, PF on wages or capped) is solved and
the one that agrees with its own result is used.

LTA and reimbursements (rules.REIMBURSEMENTS, the Reference Master) apply
only to cadres D, C and M with a CTC above Rs 12 lakh. Those picked Yes are
paid in preference order out of the Special Allowance left, each only if
it fits in full; Old Regime items are left out under the New Regime.
The CTC per month must be at least the work location's state minimum wage
(rules.MIN_WAGES); one below it is refused.

Every component is a whole rupee a month, and annual = monthly x 12 - except
that the few rupees monthly rounding leaves over go into the yearly Special
Allowance, so the TOTAL CTC per annum is exactly the uploaded figure.

Pure: takes plain values, returns plain data. Nothing here touches the
database except through the settings object passed in.
"""
import re
from decimal import ROUND_HALF_UP, Decimal

from . import rules
from .columns import OPTION_CADRES


def _r(x):
    """Whole rupees, half up."""
    return int(Decimal(str(x)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _norm(text):
    return re.sub(r'[^a-z0-9]', '', (text or '').lower())


def normalise_cadre(text):
    t = (text or '').strip().upper()[:1]
    return t if t in rules.CADRES else ''


def normalise_category(text):
    k = _norm(text)
    if k in ('sales', 'sl'):
        return 'Sales'
    if k in ('ho', 'headoffice', 'corporate', 'corporateoffice'):
        return 'HO'
    if k in ('plant', 'rk', 'factory', 'roorkee', 'works'):
        return 'Plant'
    return ''


def state_for(location):
    """The state a Work Location is in, or '' if it can't be told.
    Matches a state name or a known city anywhere in the text, so "Delhi HO",
    "Roorkee Plant" and "UTTAR PRADESH" all resolve."""
    k = _norm(location)
    if not k:
        return ''
    states = {_norm(st): st for st in rules.MIN_WAGES}
    if k in states:
        return states[k]
    if k in rules.LOCATION_ALIASES:
        return rules.LOCATION_ALIASES[k]
    # Longest names first, so "Andhra Pradesh" wins over a shorter match.
    for key in sorted(list(states) + list(rules.LOCATION_ALIASES), key=len, reverse=True):
        if len(key) > 3 and key in k:
            return states.get(key) or rules.LOCATION_ALIASES[key]
    return ''


# Ways people write the two Sales departments in the VPP table.
_DEPT_ALIASES = {
    'generaltrade': 'General Trade', 'gt': 'General Trade',
    'alternatechannels': 'Alternate Channels', 'alternatechannel': 'Alternate Channels',
    'ac': 'Alternate Channels',
}


def _sales_department(text):
    k = _norm(text)
    if k in _DEPT_ALIASES:
        return _DEPT_ALIASES[k]
    for key, dept in _DEPT_ALIASES.items():
        if len(key) > 2 and key in k:
            return dept
    return ''


_WORD_FIXES = {'asst': 'assistant', 'dy': 'deputy', 'sr': 'senior', 'mgr': 'manager'}


def _words(text):
    """' deputy manager finance ' from "Dy. Manager (Finance)" - lower case,
    punctuation to spaces, common short forms spelt out, padded so a phrase
    can be found as whole words with `' x y ' in`."""
    words = re.sub(r'[^a-z0-9]+', ' ', (text or '').lower()).split()
    return ' ' + ' '.join(_WORD_FIXES.get(w, w) for w in words) + ' '


_CADRE_RANK = {'W': 0, 'O': 1, 'M': 2, 'C': 3, 'D': 4}


def _grade_rank(grade):
    """(cadre rank, number) so grades compare: W1 < O5 < M1 < M6 < C1 < C5 < D1."""
    g = (grade or '').strip().upper()
    if not g or g[0] not in _CADRE_RANK:
        return None
    digits = re.sub(r'\D', '', g)
    return _CADRE_RANK[g[0]], int(digits) if digits else 0


def _sales_by_grade(g, department, designation):
    """P & C's grade table, for Sales designations the policy doesn't name."""
    rows = [r for r in rules.VPP_SALES if r[0] == g]
    if not rows:
        return 0, (f'No Sales VPP: the designation is not one the Variable Pay Policy names '
                   f'(Business Head, ZSM / RSM / KAM, ASM) and grade {g or "-"} is not in the grade table.')
    if len(rows) > 1:
        dept = _sales_department(department)
        by_dept = [r for r in rows if r[1] is None or r[1] == dept] if dept else []
        by_desig = [r for r in rows if _norm(r[2]) == _norm(designation)]
        rows = by_dept if len(by_dept) == 1 else by_desig
        if len(rows) != 1:
            options = ' or '.join(f'{r[1]} ({r[3]}%)' for r in rules.VPP_SALES if r[0] == g)
            return 0, f'Sales grade {g} has two VPP rates - set Department to {options} to pick one.'
    r_grade, r_dept, _desig, pct = rows[0]
    return pct, f'Sales · grade {r_grade}' + (f' · {r_dept}' if r_dept else '')


def vpp_percent(category, grade, department, designation, date_of_joining=None):
    """(percent, description) under the Variable Pay Policy. With a percent,
    the description names the rule that gave it; with 0, it says why.

    Sales: grade M5 and above (M5, M6, C1-C5, D) 15%. Below that, by
    designation - Business Head / OT Head 15%, ZSM / RSM / KAM 12%,
    ASM 10% (not for an ASM who joined before 01-06-2024). A designation the
    policy doesn't name falls back to P & C's grade table.
    HO (Corporate) and Plant (Factory): 8% from grade M3 up, except Deputy
    and Assistant Managers.
    Managerial roles and above only (cadres D, C, M)."""
    g = (grade or '').strip().upper()
    rank = _grade_rank(g)
    if category not in ('Sales', 'HO', 'Plant'):
        return 0, ''
    if rank is not None and rank[0] < _CADRE_RANK['M']:
        return 0, f'No VPP: the Variable Pay Policy covers managerial roles and above, not grade {g}.'
    words = _words(designation)
    if category == 'Sales':
        if rank is not None and rank >= _grade_rank(rules.VPP_SALES_SENIOR_FROM_GRADE):
            return rules.VPP_SALES_SENIOR, f'Sales · grade {rules.VPP_SALES_SENIOR_FROM_GRADE} and above'
        for pct, role, phrases in rules.VPP_SALES_BY_DESIGNATION:
            if any(f' {p} ' in words for p in phrases):
                if role == 'ASM' and date_of_joining and \
                        (date_of_joining.year, date_of_joining.month, date_of_joining.day) \
                        < rules.VPP_ASM_JOINED_FROM:
                    return 0, ('No VPP: ASMs who joined before 01-06-2024 are excluded '
                               '(Variable Pay Policy, FY 24-25).')
                return pct, f'Sales · {role}'
        return _sales_by_grade(g, department, designation)
    name = 'Corporate' if category == 'HO' else 'Factory'
    excluded = next((p for p in rules.VPP_NON_SALES_EXCLUDED if f' {p} ' in words), None)
    if excluded:
        return 0, f'No VPP: {excluded.title()}s are excluded for {name} (Variable Pay Policy).'
    if rank is None or rank < _grade_rank(rules.VPP_NON_SALES_FROM_GRADE):
        return 0, f'No VPP: for {name} the Variable Pay Policy starts at grade M3, and this is {g or "-"}.'
    return rules.VPP_NON_SALES, f'{name} · M3 and above'


def _solve(base, med, hra, s):
    """(gross, esi, mediclaim, pf) for base = everything left after VPP,
    Gift and Gratuity. Tries the four cases and keeps the consistent one."""
    esi_rate = rules.ESI_PERCENT / 100
    pf_rate = rules.PF_PERCENT / 100
    esi_limit, pf_limit = s.esi_bonus_gross_limit, s.pf_wage_ceiling
    pf_cap = pf_limit * pf_rate
    cases = [
        # (ESI?, PF on wages?, gross)
        (False, False, base - med - pf_cap),
        (False, True, (base - med + pf_rate * hra) / (1 + pf_rate)),
        (True, True, (base + pf_rate * hra) / (1 + esi_rate + pf_rate)),
        (True, False, (base - pf_cap) / (1 + esi_rate)),
    ]
    for esi_on, pf_on, g in cases:
        if (g <= esi_limit) == esi_on and (g < pf_limit) == pf_on:
            break
    else:                       # on a boundary, rounding can leave no exact fit
        esi_on, pf_on, g = cases[1]
    pf = _r(max(g - hra, 0) * pf_rate) if pf_on else _r(pf_cap)
    esi = _r(g * esi_rate) if esi_on else 0
    med = 0 if esi_on else med
    return base - esi - med - pf, esi, med, pf


def _line(label, monthly, note=''):
    return {'label': label, 'monthly': monthly, 'annual': monthly * 12, 'note': note}


def _inr(n):
    """Indian digit grouping: 3,36,000."""
    head, tail = str(int(n)), ''
    if len(head) > 3:
        head, tail = head[:-3], ',' + head[-3:]
        while len(head) > 2:
            head, tail = head[:-2], ',' + head[-2:] + tail
    return head + tail


def calculate(*, ctc_annual, cadre, grade, category, department='', designation='',
              work_location='', children=0, options=None, tax_regime='',
              date_of_joining=None, settings):
    """The full break-up as plain data, or {'error': ...} if it can't be
    worked out. `settings` is a CtcSettings. `options` is the Gross Earnings
    answered on the template, {key: True/False}: an item answered No is not
    paid and its money stays in Special Allowance."""
    s = settings
    cadre = normalise_cadre(cadre)
    category = normalise_category(category)
    grade = (grade or '').strip().upper()
    notes = []
    if not cadre:
        return {'error': 'Cadre must be one of D, C, M, O or W.'}
    if ctc_annual is None or ctc_annual <= 0:
        return {'error': 'CTC is missing.'}
    ctc_annual = _r(ctc_annual)
    if not category:
        notes.append('Category (Sales / HO / Plant) is missing, so no VPP was applied.')

    # -- off the top: VPP and Gift ------------------------------------------
    pct, vpp_rule = vpp_percent(category, grade, department, designation, date_of_joining)
    if not pct and vpp_rule:
        notes.append(vpp_rule)
    vpp_annual = _r(ctc_annual * pct / 100)
    gift_m = rules.GIFT_MONTHLY[cadre]
    F = _r((ctc_annual - vpp_annual - gift_m * 12) / 12)
    if F <= 0:
        return {'error': 'CTC is too low to cover VPP and the gift reimbursement.'}

    med = rules.MEDICLAIM.get(grade)
    if med is None:
        med = 0
        notes.append(f'No mediclaim rate for grade "{grade or "-"}", so it was taken as 0.')

    state = state_for(work_location)
    min_wage = rules.MIN_WAGES.get(state, 0)
    ctc_m = _r(float(ctc_annual) / 12)
    if ctc_m < min_wage:                                 # P & C: checked on the CTC per month
        return {'error': f"Below {state}'s minimum wage: the CTC is Rs {_inr(ctc_m)} a month, under "
                         f"{state}'s minimum wage of Rs {_inr(min_wage)} a month. Raise the CTC to at "
                         f'least Rs {_inr(min_wage * 12)} a year.'}
    if not state:
        notes.append(f'No minimum wage found for work location "{work_location or "-"}", '
                     'so Basic was not checked against one.')

    counted = min(max(int(children or 0), 0), rules.CEA_MAX_CHILDREN)
    cea = rules.CEA_PER_CHILD[cadre] * counted
    basic = _r(float(ctc_annual) / 12 * rules.BASIC_PERCENT / 100)
    hra = full_hra = _r(basic * rules.HRA_PERCENT[cadre] / 100)
    grat = _r(basic * float(s.gratuity_percent) / 100)
    gross, esi, med, pf = _solve(F - grat, med, full_hra, s)
    bonus = 0

    # -- Special Allowance: what Gross leaves after Basic, HRA and CEA ------
    def balance():
        return gross - basic - hra - cea

    reduced = []
    if balance() < 0 and cea:
        cea -= min(cea, -balance())
        reduced.append('Child Education Allowance')
    if balance() < 0 and hra:
        hra -= min(hra, -balance())
        reduced.append('House Rent Allowance')
    if reduced:
        notes.append(f'After Basic ({rules.BASIC_PERCENT}% of CTC) there was not room for everything, '
                     'so these were reduced: ' + ', '.join(reduced) + '.')
    if balance() < 0:
        return {'error': f'CTC is too low for this cadre: Basic ({rules.BASIC_PERCENT}% of CTC, '
                         f'Rs {_inr(basic)}) is more than the Gross Salary of Rs {_inr(gross)}.'}
    spl = balance()

    # -- LTA and reimbursements: picked, in preference order, out of Special,
    #    each only if it fits in full ---------------------------------------
    eligible = cadre in OPTION_CADRES and ctc_annual > rules.REIMBURSEMENT_MIN_CTC
    picked = (options or {}) if eligible else {}
    if options and any(options.values()) and not eligible:
        notes.append('LTA and reimbursements apply only to cadres D, C and M with a CTC above '
                     f'Rs {_inr(rules.REIMBURSEMENT_MIN_CTC)}.')
    car_lease = picked.get('car') is True               # as per the car lease policy
    # Old Regime gets the Old Regime items and the Both Regime ones; New
    # Regime and Both Regime get only the Both Regime ones.
    old_regime = _norm(tax_regime) == 'oldregime'
    paid, no_room, old_only = {}, [], []
    for key, label, amounts, regime in rules.REIMBURSEMENTS:
        amount = amounts[cadre] if picked.get(key) is True else 0
        if amount and regime == 'old' and not old_regime:
            old_only.append(label)
            amount = 0
        if amount > spl:
            no_room.append(f'{label} (Rs {_inr(amount)})')
            amount = 0
        paid[key] = amount
        spl -= amount
    if old_only:
        notes.append(f'{tax_regime or "No tax regime given"}, so these Old Regime only items were not '
                     'given: ' + ', '.join(old_only) + '.')
    if no_room:
        notes.append('Not enough Special Allowance left, so these were not given: '
                     + ', '.join(no_room) + '.')
    lta = paid.pop('lta')
    reimb = [(key, label, paid[key]) for key, label, _a, _r in rules.REIMBURSEMENTS if key in paid]
    hra_note = (f'{rules.HRA_PERCENT[cadre]}% of Basic' if hra == full_hra
                else f'Reduced from {rules.HRA_PERCENT[cadre]}% of Basic to fit')

    earnings = [
        _line('Basic Salary (Inclusive of DA/VDA)', basic,
              f'{rules.BASIC_PERCENT}% of CTC'),
        _line('House Rent Allowance (HRA)', hra, hra_note),
        _line('Child Education Allowance (CEA)', cea,
              f'Rs {rules.CEA_PER_CHILD[cadre]:,} x {counted} child{"ren" if counted != 1 else ""}'
              if cea else ''),
        _line('Leave Travel Allowance (LTA)', lta, 'Cadre slab' if lta else ''),
        _line('Special Allowance (Flexi Pay)', spl, 'Gross less Basic, HRA, CEA, LTA and reimbursements'),
    ]
    gross_earnings = sum(r['monthly'] for r in earnings)
    # Every reimbursement, zeros included and keyed, so the Annexure A PDF can
    # print its fixed rows; the screen shows only the ones that apply.
    reimbursements = [{**_line(label, amount), 'key': key} for key, label, amount in reimb]
    benefits = [
        _line('Employer PF Contribution', pf,
              f'{rules.PF_PERCENT}% of (Gross Salary - HRA)' if gross < s.pf_wage_ceiling else
              f'Rs {_inr(pf)} (Gross Salary Rs {_inr(s.pf_wage_ceiling)} or more)'),
        _line('Employer ESI Contribution', esi,
              f'{rules.ESI_PERCENT}% of Gross Salary (up to Rs {_inr(s.esi_bonus_gross_limit)})'
              if esi else 'Not applicable'),
        _line('Mediclaim Charges', med,
              'ESI applies instead' if esi else (f'Grade {grade}' if grade else '')),
        _line('Statutory Bonus', bonus, 'Not applicable'),
        _line('Gratuity', grat, f'{s.gratuity_percent}% of Basic' if grat else ''),
    ]
    benefits_total = sum(r['monthly'] for r in benefits)
    fixed = gross + benefits_total
    other = [
        {'label': 'Variable / Performance Pay', 'monthly': _r(vpp_annual / 12), 'annual': vpp_annual,
         'note': f'{pct}% of CTC ({vpp_rule}), paid quarterly' if pct else 'Not applicable'},
        {'label': 'Gift Reimbursement', 'monthly': gift_m, 'annual': gift_m * 12, 'note': 'Paid quarterly' if gift_m else 'Not applicable'},
    ]
    # Rounding each month to whole rupees leaves the year a few rupees off
    # the uploaded CTC (monthly x 12 vs the exact figure). Put the difference
    # into the yearly Special Allowance - or Basic, if Special is nil - so
    # the TOTAL CTC per annum is exactly what was uploaded.
    diff = (ctc_annual - vpp_annual - gift_m * 12) - fixed * 12
    if diff:
        spl_line, basic_line = earnings[4], earnings[0]
        target = spl_line if spl_line['annual'] + diff >= 0 else basic_line
        target['annual'] += diff

    gross_earnings_annual = sum(r['annual'] for r in earnings)
    gross_salary_annual = gross_earnings_annual + sum(r['annual'] for r in reimbursements)
    benefits_annual = sum(r['annual'] for r in benefits)
    fixed_annual = gross_salary_annual + benefits_annual
    total_annual = fixed_annual + vpp_annual + gift_m * 12

    return {
        'cadre': cadre, 'cadreName': rules.CADRES[cadre], 'grade': grade, 'category': category,
        'state': state, 'minWage': min_wage,
        'ctcAnnual': ctc_annual, 'vppPercent': pct,
        'earnings': earnings, 'grossEarnings': gross_earnings, 'grossEarningsAnnual': gross_earnings_annual,
        'reimbursements': reimbursements, 'grossSalary': gross, 'grossSalaryAnnual': gross_salary_annual,
        'benefits': benefits, 'benefitsTotal': benefits_total, 'benefitsTotalAnnual': benefits_annual,
        'fixedMonthly': fixed, 'fixedAnnual': fixed_annual,
        'otherPayments': other,
        'totalCtcMonthly': _r(total_annual / 12), 'totalCtcAnnual': total_annual,
        'carLease': car_lease,
        'notes': notes,
    }
