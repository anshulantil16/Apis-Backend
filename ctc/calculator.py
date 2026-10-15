"""Work out a candidate's compensation break-up from their total CTC.

    Total CTC (per annum, as uploaded)
      - Variable / Performance Pay      VPP % of total CTC (rules.VPP_*)
      - Gift Reimbursement              Rs 1,250 a month, paid quarterly
      = Fixed CTC, per month (F)

    F = Gross Salary + employer PF + ESI + Mediclaim + Statutory Bonus + Gratuity
    Basic             = 50% of Gross Salary   (W: minimum wage if that is higher)
    HRA               = 50% of Basic          (W: 20%)
    CEA, LTA and the cadre's reimbursements are fixed amounts
    Special Allowance = Gross Salary - everything else in it (the balance)

Basic depends on Gross, and Gross on PF and Bonus, which depend on Basic -
so the two are solved together; the loop settles in a handful of passes.
ESI and Statutory Bonus apply only when Gross Salary is within the limit
(Rs 21,000 a month by default).

Every component is a whole rupee a month, and annual = monthly x 12.
Special Allowance absorbs the rounding, so the fixed part always adds up.

Pure: takes plain values, returns plain data. Nothing here touches the
database except through the settings object passed in.
"""
import re
from decimal import ROUND_HALF_UP, Decimal

from . import rules


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


def vpp_percent(category, grade, department, designation):
    """(percent, rule description) - 0 when no rule covers this person."""
    g = (grade or '').strip().upper()
    if category == 'Sales':
        for r_grade, r_dept, r_desig, pct in rules.VPP_SALES:
            if g != r_grade or _norm(designation) != _norm(r_desig):
                continue
            if r_dept and _norm(department) and _norm(department) != _norm(r_dept):
                continue
            return pct, f'Sales · {r_desig} · {r_grade}'
        return 0, ''
    grades, pct = rules.VPP_BY_GRADE.get(category, (set(), 0))
    if g in grades:
        return pct, f'{category} · {g}'
    return 0, ''


def _solve(F, med, cadre, s, small):
    """Monthly gross for one branch: small=True applies ESI and Bonus."""
    gross = F - med
    for _ in range(60):
        basic = gross * rules.BASIC_PERCENT / 100
        if cadre == 'W':
            basic = max(basic, s.w_min_wage)
        pf = min(basic, s.pf_wage_ceiling) * rules.PF_PERCENT / 100
        grat = basic * float(s.gratuity_percent) / 100
        bonus = basic * rules.BONUS_PERCENT / 100 if small else 0
        # ESI is a share of gross itself: gross + esi + the rest = F
        new = (F - med - pf - grat - bonus) / (1 + (rules.ESI_PERCENT / 100 if small else 0))
        if abs(new - gross) < 0.001:
            return new
        gross = new
    return gross


def _line(label, monthly, note=''):
    return {'label': label, 'monthly': monthly, 'annual': monthly * 12, 'note': note}


def calculate(*, ctc_annual, cadre, grade, category, department='', designation='', settings):
    """The full break-up as plain data, or {'error': ...} if it can't be
    worked out. `settings` is a CtcSettings."""
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
    pct, vpp_rule = vpp_percent(category, grade, department, designation)
    vpp_annual = _r(ctc_annual * pct / 100)
    gift_m = rules.GIFT_MONTHLY
    F = _r((ctc_annual - vpp_annual - gift_m * 12) / 12)
    if F <= 0:
        return {'error': 'CTC is too low to cover VPP and the gift reimbursement.'}

    med = rules.MEDICLAIM.get(grade)
    if med is None:
        med = 0
        notes.append(f'No mediclaim rate for grade "{grade or "-"}", so it was taken as 0.')

    # -- Gross, Basic and the employer items, solved together ---------------
    gross = _solve(F, med, cadre, s, small=False)
    small = gross <= s.esi_bonus_gross_limit
    if small:
        gross = _solve(F, med, cadre, s, small=True)

    basic = _r(max(gross * rules.BASIC_PERCENT / 100, s.w_min_wage if cadre == 'W' else 0))
    pf = _r(min(basic, s.pf_wage_ceiling) * rules.PF_PERCENT / 100)
    grat = _r(basic * float(s.gratuity_percent) / 100)
    bonus = _r(basic * rules.BONUS_PERCENT / 100) if small else 0
    esi = _r(gross * rules.ESI_PERCENT / 100) if small else 0
    gross = F - pf - esi - med - bonus - grat        # exact, so the fixed part adds up

    hra = _r(basic * rules.HRA_PERCENT[cadre] / 100)
    cea = rules.CEA_MONTHLY[cadre]
    lta = s.lta_for(cadre)

    reimb = []
    for key, label, amounts in rules.REIMBURSEMENTS:
        amount = amounts[cadre]
        if amount is None:                               # "Actual" -> settings
            amount = s.actual_for(key)
        reimb.append([key, label, int(amount)])

    # -- Special Allowance is the balance; trim reimbursements if short -----
    def balance():
        return gross - basic - hra - cea - lta - sum(a for _k, _l, a in reimb)

    reduced = []
    for item in reversed(reimb):
        if balance() >= 0:
            break
        if item[2]:
            item[2] -= min(item[2], -balance())
            reduced.append(item[1])
    if balance() < 0 and cea:
        cea -= min(cea, -balance())
        reduced.append('Child Education Allowance')
    if reduced:
        notes.append('CTC is too low for the full cadre entitlement, so these were reduced: '
                     + ', '.join(reduced) + '.')
    if balance() < 0:
        return {'error': 'CTC is too low for this cadre: Basic, HRA and LTA alone exceed the gross salary.'}
    spl = balance()

    earnings = [
        _line('Basic Salary (Inclusive of DA/VDA)', basic, f'{rules.BASIC_PERCENT}% of Gross Salary'),
        _line('House Rent Allowance (HRA)', hra, f'{rules.HRA_PERCENT[cadre]}% of Basic'),
        _line('Child Education Allowance (CEA)', cea, 'Max 2 children' if cea else ''),
        _line('Leave Travel Allowance (LTA)', lta, 'As per cadre' if lta else ''),
        _line('Special Allowance (Flexi Pay)', spl, 'Balance of Gross Salary'),
    ]
    gross_earnings = sum(r['monthly'] for r in earnings)
    reimbursements = [_line(label, amount) for _k, label, amount in reimb if amount]
    benefits = [
        _line('Employer PF Contribution', pf,
              f'{rules.PF_PERCENT}% of Basic (wages capped at Rs {s.pf_wage_ceiling:,})'),
        _line('Employer ESI Contribution', esi,
              f'{rules.ESI_PERCENT}% of Gross Salary' if small else 'Not applicable'),
        _line('Mediclaim Charges', med, f'Grade {grade}' if grade else ''),
        _line('Statutory Bonus', bonus, f'{rules.BONUS_PERCENT}% of Basic' if small else 'Not applicable'),
        _line('Gratuity', grat, f'{s.gratuity_percent}% of Basic' if grat else ''),
    ]
    benefits_total = sum(r['monthly'] for r in benefits)
    fixed = gross + benefits_total
    other = [
        {'label': 'Variable / Performance Pay', 'monthly': _r(vpp_annual / 12), 'annual': vpp_annual,
         'note': f'{pct}% of CTC ({vpp_rule}), paid quarterly' if pct else 'Not applicable'},
        {'label': 'Gift Reimbursement', 'monthly': gift_m, 'annual': gift_m * 12, 'note': 'Paid quarterly'},
    ]
    total_annual = fixed * 12 + vpp_annual + gift_m * 12
    if total_annual != ctc_annual:
        notes.append(f'Total differs from the uploaded CTC by Rs {total_annual - ctc_annual:+,} '
                     'from rounding each month to whole rupees.')

    return {
        'cadre': cadre, 'cadreName': rules.CADRES[cadre], 'grade': grade, 'category': category,
        'ctcAnnual': ctc_annual, 'vppPercent': pct,
        'earnings': earnings, 'grossEarnings': gross_earnings,
        'reimbursements': reimbursements, 'grossSalary': gross,
        'benefits': benefits, 'benefitsTotal': benefits_total,
        'fixedMonthly': fixed, 'fixedAnnual': fixed * 12,
        'otherPayments': other,
        'totalCtcMonthly': _r(total_annual / 12), 'totalCtcAnnual': total_annual,
        'notes': notes,
    }
