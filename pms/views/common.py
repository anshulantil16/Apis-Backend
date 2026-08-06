"""Shared helpers used by more than one PMS subsystem.

Anything here is imported by two or more of simulator / offer_letters /
warning_letters. If something is only used by one of them it belongs in that
module, not this one.
"""
import io
import os
import re
import secrets
import openpyxl
from django.core.cache import cache
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from ..models import (PMSEmployee, PMSAuditLog, PMSSettings, OfferLetter,
                      WarningLetter, GRADE_META)


# ── Fixed display orders for distribution charts (not sorted by count) ────────
# Cadre/Band hierarchy: Director → CXO/HOD → Middle Mgmt → Officer → Workforce
BAND_ORDER = ['D', 'C5', 'C4', 'C3', 'C2', 'C1',
              'M6', 'M5', 'M4', 'M3', 'M2', 'M1',
              'O5', 'O4', 'O3', 'O2', 'O1',
              'W4', 'W3', 'W2', 'W1']
_BAND_IDX = {b: i for i, b in enumerate(BAND_ORDER)}


def _band_sort_key(name):
    return (_BAND_IDX.get(str(name).strip().upper(), len(BAND_ORDER)), str(name))


def _location_sort_key(name):
    """Order: GTR01…GTR09 (numeric), then HO, then Plant, then everything else."""
    n = str(name).strip().upper()
    m = re.match(r'GTR\s*0*(\d+)', n)
    if m:
        return (0, int(m.group(1)), n)
    if n in ('HO', 'HEAD OFFICE', 'H.O.'):
        return (1, 0, n)
    if n == 'PLANT':
        return (2, 0, n)
    return (3, 0, n)


# Imported CTC is MONTHLY → multiply for ANNUAL display/export everywhere.
# (One-time rewards and % fields are never annualised.)
CTC_ANNUAL_MULT = 12


def _ann(v):
    """Annualise a single money value (monthly → annual). Non-numbers pass through."""
    return round(v * CTC_ANNUAL_MULT, 2) if isinstance(v, (int, float)) else v


# ── PMS Simulator login (email OTP) ───────────────────────────────────────────

def _mask_email(email):
    try:
        name, dom = email.split('@', 1)
        head = name[:2] if len(name) > 2 else name[:1]
        return f"{head}{'*' * max(1, len(name) - len(head))}@{dom}"
    except Exception:
        return email


def _clip_to_field(model, field_name, value):
    """Defensively truncate a string to the model field's actual max_length so
    a long free-text value from an Excel column (e.g. "Performance Rating")
    can never crash the whole letter with a DB "Data too long" error — it is
    safely cut off instead of losing the letter and vanishing from history."""
    if not isinstance(value, str) or not value:
        return value
    try:
        max_len = model._meta.get_field(field_name).max_length
    except Exception:
        max_len = None
    return value[:max_len] if max_len and len(value) > max_len else value

