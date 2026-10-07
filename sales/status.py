"""Red / Amber / Green, defined once.

Taken from the APIS AI Sales & Collection Control Tower blueprint, section 7,
which is the company's own statement of when a number needs somebody to act:

    RED     achievement < 70%
    AMBER   achievement 70-90%
    GREEN   achievement > 100%

Two things about that as written, both deliberate decisions here rather than
silent ones.

The bands leave 90-100% unnamed. A branch at 94% of plan is not RED by the
rule and not GREEN by the rule, and the gap has to be closed somewhere or
every screen closes it differently. It is closed as AMBER: the blueprint
defines GREEN as "achievement >100% OR on/above required run-rate", so the
threshold it actually cares about is whether the business is on course, and
94% with a month to go is a watch, not a pass. The upper AMBER bound is
therefore 100, not 90.

And the blueprint's RED also covers "material overdue >90 days" and "zero
sales for a defined period". Those come off collections and a daily calendar,
neither of which is loaded: this product carries P1 primary sales only. So
this module answers the achievement limb and nothing else, and says so,
rather than implying a RED here means everything the blueprint means by RED.
"""

RED = 'red'
AMBER = 'amber'
GREEN = 'green'

# The achievement limb of the blueprint's rule. Upper AMBER bound is 100
# rather than 90 -- see the module docstring.
#
# Settable, and the reason is not politeness about someone else's numbers.
# Run against the figures actually on this dashboard, these thresholds put
# five of the first seven branches in RED and none in GREEN. A screen where
# everything is red is a screen nobody reads by the second week: the colour
# stops meaning "act on this" and starts meaning "this is the dashboard".
#
# That does not make the thresholds wrong. It means one of two things is
# true -- the AOP is a stretch plan the business habitually runs under, or
# something is understating revenue -- and neither is mine to decide from
# here. So the blueprint's numbers stand as the default, and moving them is
# a line in settings rather than a code change and a deploy:
#
#     SALESIQ_RED_BELOW = 55
#     SALESIQ_GREEN_AT  = 95
#
# Read per call, not bound at import, so the change takes without a restart.
#
# Whatever they are set to, the screen prints the band it used beside the
# status, and the org view reports how many branches landed in each -- so a
# wall of red is legible as a statement about the plan rather than as
# nineteen separate accusations.
DEFAULT_RED_BELOW = 70.0
DEFAULT_GREEN_AT = 100.0


def thresholds():
    """The bands in force.

    Read when asked rather than at import. Bound once at import, a server
    that moved the red line would have had to be restarted for it to take,
    and -- the reason it was actually changed -- every message built from the
    bounds would have frozen the defaults into itself, so a dashboard running
    at 55 went on printing "Under 70% of AOP" beside the status.
    """
    red, green = DEFAULT_RED_BELOW, DEFAULT_GREEN_AT
    try:
        from django.conf import settings
        red = float(getattr(settings, 'SALESIQ_RED_BELOW', red))
        green = float(getattr(settings, 'SALESIQ_GREEN_AT', green))
    except Exception:
        pass
    return {'red_below': red, 'green_at': green}

LABELS = {
    RED:   'Needs intervention',
    AMBER: 'Watch',
    GREEN: 'On or ahead of plan',
}

# What each band means in the blueprint's own terms, for the screen to show
# beside the colour so a status is never just a colour.
def _meaning(s):
    t = thresholds()
    return {
        RED:   f'Under {t["red_below"]:.0f}% of AOP',
        AMBER: f'{t["red_below"]:.0f}% to {t["green_at"]:.0f}% of AOP',
        GREEN: f'{t["green_at"]:.0f}% of AOP or better',
    }[s]


def rag(pct):
    """-> 'red' / 'amber' / 'green', or None where there is no plan.

    None is not a fourth status. A branch with no AOP against it has not
    passed and has not failed; colouring it green because nothing was asked
    of it, or red because it cleared nothing, would both be inventions. The
    screen shows no status at all in that case.
    """
    if pct is None:
        return None
    t = thresholds()
    if pct < t['red_below']:
        return RED
    if pct < t['green_at']:
        return AMBER
    return GREEN


def band(pct):
    """-> {'status', 'label', 'meaning'} or None, for a response."""
    s = rag(pct)
    if s is None:
        return None
    return {'status': s, 'label': LABELS[s], 'meaning': _meaning(s),
            'thresholds': thresholds()}


def tally(pcts):
    """-> {'red': n, 'amber': n, 'green': n, 'unrated': n, 'total': n}.

    So a screen can say "19 of 23 are under the red line" in one place
    instead of leaving the reader to count colours down a long list -- and so
    that a wall of red reads as what it is, which is a statement about how
    the plan was set rather than about nineteen individual people.
    """
    out = {RED: 0, AMBER: 0, GREEN: 0, 'unrated': 0}
    for p in pcts:
        out[rag(p) or 'unrated'] += 1
    out['total'] = sum(out.values())
    return out

