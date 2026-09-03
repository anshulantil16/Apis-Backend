"""Render an aware datetime in India time, not the UTC it is stored in.

USE_TZ=True with TIME_ZONE='UTC' (config.settings) means every DateTimeField
is stored and handed back in UTC - that setting is the storage zone, not a
display preference, and nothing in this project ever calls
timezone.activate() to switch the active zone. Calling .strftime() straight
on that value prints UTC clock digits as if they were already local: every
timestamp is off by IST's 5:30 offset, and anything from 18:30 UTC onward
lands on the wrong calendar day. This project is India-only, so IST is the
one zone display ever needs to convert to.
"""
from zoneinfo import ZoneInfo

from django.utils import timezone

IST = ZoneInfo('Asia/Kolkata')


def local_str(value, fmt):
    """value.strftime(fmt), converted to IST first. None-safe."""
    if not value:
        return None
    return timezone.localtime(value, IST).strftime(fmt)
