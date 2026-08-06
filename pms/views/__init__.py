"""PMS views package.

Split out of a single 2,700-line module so it is obvious what controls what:

    common.py           helpers shared by more than one subsystem
    auth.py             PMS Simulator OTP login
    simulator.py        employee master, scoring, increments, import/export
    offer_letters.py    appraisal / compensation-revision letters
    warning_letters.py  warning / disciplinary letters

Everything is re-exported here, so `from .views import SomeView` and
`from pms.views import helper` keep working exactly as before the split.
"""
from .common import *          # noqa: F401,F403
from .auth import *            # noqa: F401,F403
from .simulator import *       # noqa: F401,F403
from .offer_letters import *   # noqa: F401,F403
from .warning_letters import * # noqa: F401,F403

# `import *` skips underscore-prefixed names, but urls.py and the letter
# modules rely on several of them — re-export explicitly.
from .common import _ann, _clip_to_field, _mask_email, _band_sort_key, _location_sort_key
from .auth import _pms_allowed_emails, _pms_email_authorized
from .simulator import _apply_global_mgmt
from .offer_letters import _process_offer_batch, _letter_filename
from .warning_letters import (_process_warning_batch, _warning_filename,
                              _normalise_warning_type, _split_emails,
                              _build_warning_pdf, _warning_mail_connection,
                              _generate_and_maybe_send)
