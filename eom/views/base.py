"""The base view for every EOM endpoint that is not the login.

EOM kept its own OTP login from before the portal, and that login used
to issue nothing: it proved who you were once, at the door, and every request
afterwards named an employee id in the URL and was believed. A sweep on
14-10-2026 found /api/eom/employees/ answering anybody at all --
names, employee ids, email addresses and phone numbers -- with no credential
of any kind.

The gate itself lives in accounts.auth.AppSessionRequiredAPIView; this only
says which app it is. See that class for why enforcement is behind a flag.
"""
from accounts.auth import AppSessionRequiredAPIView


class EOMAPIView(AppSessionRequiredAPIView):
    APP = 'eom'
