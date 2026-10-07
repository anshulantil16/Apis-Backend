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
RED_BELOW = 70.0
GREEN_AT = 100.0

LABELS = {
    RED:   'Needs intervention',
    AMBER: 'Watch',
    GREEN: 'On or ahead of plan',
}

# What each band means in the blueprint's own terms, for the screen to show
# beside the colour so a status is never just a colour.
MEANING = {
    RED:   f'Under {RED_BELOW:.0f}% of AOP',
    AMBER: f'{RED_BELOW:.0f}% to {GREEN_AT:.0f}% of AOP',
    GREEN: f'{GREEN_AT:.0f}% of AOP or better',
}


def rag(pct):
    """-> 'red' / 'amber' / 'green', or None where there is no plan.

    None is not a fourth status. A branch with no AOP against it has not
    passed and has not failed; colouring it green because nothing was asked
    of it, or red because it cleared nothing, would both be inventions. The
    screen shows no status at all in that case.
    """
    if pct is None:
        return None
    if pct < RED_BELOW:
        return RED
    if pct < GREEN_AT:
        return AMBER
    return GREEN


def band(pct):
    """-> {'status', 'label', 'meaning'} or None, for a response."""
    s = rag(pct)
    if s is None:
        return None
    return {'status': s, 'label': LABELS[s], 'meaning': MEANING[s]}
