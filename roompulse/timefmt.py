"""12-hour clock for text people read (emails, messages): 14:00 -> 2:00 PM.
The API itself keeps 24-hour HH:MM, which the frontend parses."""


def ampm(t):
    """A time or datetime as '2:00 PM' (no leading zero)."""
    if t is None:
        return ''
    h = t.hour % 12 or 12
    return f'{h}:{t.minute:02d} {"AM" if t.hour < 12 else "PM"}'


def span(a, b):
    """'2:00 PM–3:00 PM'."""
    return f'{ampm(a)}–{ampm(b)}'
