"""Tests for the live attendance reader.

Every fixture below is a row shape actually seen in APIS's live tenant on
19-11-2026, not an invented one. The ones that matter are the awkward ones:
`Unswiped`, the all-`-` scaffold row, and the 31-day limit.
"""
from datetime import date

from django.test import TestCase, override_settings

from accounts.models import AppKey, PortalUser

from . import hrms_attendance as hrms
from . import views

CUTOFF = 11 * 60

# A normal day, straight off the wire.
PUNCHED = {
    'EmployeeCode': 'HO0014', 'EmployeeName': 'Sunetro Banerjee',
    'Designation': 'MANAGER', 'Grade': 'M3', 'Date': '02/11/2026',
    'InTime': '09:34', 'OutTime': '18:07', 'EarlyTime': '-', 'LateTime': '-',
    'TotalWorkingHrs': '08:33', 'OTHours': '-',
    'NormalWorkingHours': '07:56', 'NormalWorkingHoursWOHO': '-',
}

# Punched in, never punched out. 649 of these in ten days.
UNSWIPED = {
    'EmployeeCode': 'HO0015', 'EmployeeName': 'X', 'Designation': '-', 'Grade': '-',
    'Date': '05/11/2026', 'InTime': '10:45', 'OutTime': 'Unswiped',
    'EarlyTime': '-', 'LateTime': '-', 'TotalWorkingHrs': '-', 'OTHours': '-',
    'NormalWorkingHours': '-', 'NormalWorkingHoursWOHO': '-',
}

# The scaffold row the API returns for every employee on every date.
EMPTY = {
    'EmployeeCode': 'A001', 'EmployeeName': 'Admin', 'Designation': '-', 'Grade': '-',
    'Date': '01/11/2026', 'InTime': '-', 'OutTime': '-', 'EarlyTime': '-',
    'LateTime': '-', 'TotalWorkingHrs': '-', 'OTHours': '-',
    'NormalWorkingHours': '-', 'NormalWorkingHoursWOHO': '-',
}

LATE = dict(PUNCHED, EmployeeCode='SL03839', Date='04/11/2026',
            InTime='09:55', OutTime='21:26', LateTime='00:25',
            TotalWorkingHrs='11:31', NormalWorkingHours='07:35')


class ParsingTests(TestCase):

    def test_minutes_reads_hhmm(self):
        self.assertEqual(hrms.minutes('08:33'), 513)
        self.assertEqual(hrms.minutes('00:25'), 25)
        self.assertEqual(hrms.minutes('16:07'), 967)

    def test_the_dash_is_the_empty_value(self):
        self.assertIsNone(hrms.minutes('-'))
        self.assertEqual(hrms.clock('-'), '')

    def test_unswiped_is_not_a_time(self):
        """The single most likely thing to be parsed wrong: a word in a
        column that otherwise holds times."""
        self.assertIsNone(hrms.minutes('Unswiped'))
        self.assertEqual(hrms.clock('Unswiped'), '')

    def test_an_unexpected_value_costs_one_cell_not_the_fetch(self):
        self.assertIsNone(hrms.minutes('tomorrow'))
        self.assertEqual(hrms.clock('tomorrow'), '')

    def test_dates_come_back_in_a_different_format_than_they_go_in(self):
        self.assertEqual(hrms._iso('01/11/2026'), '2026-11-01')


class WindowTests(TestCase):

    def test_a_short_range_is_one_window(self):
        w = hrms.windows(date(2026, 11, 1), date(2026, 11, 10))
        self.assertEqual(w, [(date(2026, 11, 1), date(2026, 11, 10))])

    def test_thirty_one_days_is_still_one_window(self):
        """Verified against the live API: 02/10-01/11 is accepted, and that
        is 31 days inclusive. The vendor's document says 30."""
        w = hrms.windows(date(2026, 10, 2), date(2026, 11, 1))
        self.assertEqual(len(w), 1)
        self.assertEqual((w[0][1] - w[0][0]).days + 1, 31)

    def test_a_longer_range_is_split_with_no_gap_and_no_overlap(self):
        start, end = date(2026, 1, 1), date(2026, 3, 31)
        w = hrms.windows(start, end)
        self.assertGreater(len(w), 1)
        self.assertEqual(w[0][0], start)
        self.assertEqual(w[-1][1], end)
        for (_, a), (b, _) in zip(w, w[1:]):
            self.assertEqual((b - a).days, 1)
        for a, b in w:
            self.assertLessEqual((b - a).days + 1, hrms.MAX_SPAN_DAYS)

    def test_a_single_day_is_one_window(self):
        d = date(2026, 11, 5)
        self.assertEqual(hrms.windows(d, d), [(d, d)])


class NormaliseTests(TestCase):

    def test_a_normal_day(self):
        r = hrms.normalise(PUNCHED, CUTOFF)
        self.assertEqual(r['date'], '2026-11-02')
        self.assertEqual(r['inTime'], '09:34')
        self.assertEqual(r['workedMins'], 513)
        self.assertIn('present', r['flags'])
        self.assertNotIn('late', r['flags'])
        self.assertNotIn('afterCutoff', r['flags'])

    def test_an_unswiped_day_is_flagged_and_claims_no_hours(self):
        """A day that never closed. Deriving hours from one is how a
        fifteen-hour day gets into an average."""
        r = hrms.normalise(UNSWIPED, CUTOFF)
        self.assertTrue(r['outUnswiped'])
        self.assertEqual(r['outTime'], '')
        self.assertIsNone(r['workedMins'])
        self.assertIn('unswiped', r['flags'])
        self.assertIn('present', r['flags'])

    def test_an_empty_row_is_no_record_not_an_absence(self):
        """The API returns one of these for every employee on every date,
        future dates included. Counting them as absences counts the whole
        company absent on a date nobody has worked yet."""
        r = hrms.normalise(EMPTY, CUTOFF)
        self.assertTrue(r['noRecord'])
        self.assertIn('noRecord', r['flags'])
        self.assertNotIn('present', r['flags'])

    def test_hrms_lateness_is_a_duration_not_a_clock_time(self):
        r = hrms.normalise(LATE, CUTOFF)
        self.assertEqual(r['lateMins'], 25)
        self.assertIn('late', r['flags'])

    def test_the_cutoff_is_strict_so_eleven_sharp_is_on_time(self):
        on_time = hrms.normalise(dict(PUNCHED, InTime='11:00'), CUTOFF)
        late = hrms.normalise(dict(PUNCHED, InTime='11:01'), CUTOFF)
        self.assertNotIn('afterCutoff', on_time['flags'])
        self.assertIn('afterCutoff', late['flags'])

    def test_the_two_kinds_of_late_are_reported_separately(self):
        """HRMS measures against the employee's shift, the cutoff against the
        clock. Someone on an early shift can be HRMS-late at 09:55 and still
        be inside an 11:00 cutoff -- both facts are true and both are kept."""
        r = hrms.normalise(LATE, CUTOFF)
        self.assertIn('late', r['flags'])
        self.assertNotIn('afterCutoff', r['flags'])

    def test_no_cutoff_configured_means_no_cutoff_flag(self):
        r = hrms.normalise(dict(PUNCHED, InTime='23:30'), None)
        self.assertNotIn('afterCutoff', r['flags'])

    def test_dashes_in_designation_and_grade_become_empty(self):
        r = hrms.normalise(UNSWIPED, CUTOFF)
        self.assertEqual(r['designation'], '')
        self.assertEqual(r['grade'], '')

    def test_overtime_is_never_read_as_a_number(self):
        """OTHours was '-' on all 7,180 rows sampled: the tenant does not
        compute it. Nothing should invent a zero for it."""
        r = hrms.normalise(PUNCHED, CUTOFF)
        self.assertNotIn('overtime', r['flags'])

    def test_the_name_is_tidied(self):
        r = hrms.normalise(dict(PUNCHED, EmployeeName='Sushant  Gupta'), CUTOFF)
        self.assertEqual(r['name'], 'Sushant Gupta')


class FetchGuardTests(TestCase):

    def test_a_backwards_range_is_refused_before_any_request(self):
        with self.assertRaises(hrms.AttendanceError):
            hrms.fetch(date(2026, 11, 10), date(2026, 11, 1))

    def test_an_absurd_range_is_refused_before_any_request(self):
        with self.assertRaises(hrms.AttendanceError):
            hrms.fetch(date(2020, 1, 1), date(2026, 11, 1))

    @override_settings(POCKET_HRMS_TOKEN='')
    def test_no_token_is_a_clear_message_not_a_crash(self):
        with self.assertRaises(hrms.AttendanceError):
            hrms.fetch_window(date(2026, 11, 1), date(2026, 11, 2))

    @override_settings(POCKET_HRMS_TOKEN='')
    def test_not_configured_is_reported(self):
        self.assertFalse(hrms.is_configured())


class EndpointTests(TestCase):

    def setUp(self):
        self.user = PortalUser.objects.create(
            employee_code='HO0014', name='Test', email='t@apisindia.com')
        self.user.app_access = [AppKey.ATTENDANCE]
        self.user.save()

    def test_meta_refuses_an_anonymous_caller(self):
        self.assertEqual(self.client.get('/api/attendance/meta/').status_code, 401)

    def test_records_refuses_an_anonymous_caller(self):
        self.assertEqual(self.client.get('/api/attendance/records/').status_code, 401)

    def test_the_app_key_exists(self):
        self.assertEqual(AppKey.ATTENDANCE, 'attendance')


class HomeOfficeScopeTests(TestCase):
    """Delhi HO is matched on the employee CODE, not the location column.

    On 20-11-2026 the directory held 83 people with an HO code and only 40
    filed under location 'DELHI-HO'; the rest were 'DELHI' (13), blank (29)
    and one 'DEOGHAR'. Matching on location would have hidden half of head
    office and added twenty field-sales people who sit under 'DELHI'.
    """

    def test_the_prefixes_and_label_are_configurable(self):
        """Comma-separated since 23-11-2026 — one prefix dropped the seven
        NA-coded staff out of the view entirely."""
        from django.conf import settings
        self.assertEqual(getattr(settings, 'ATTENDANCE_HO_CODE_PREFIX', ''), 'HO,NA')
        self.assertEqual(getattr(settings, 'ATTENDANCE_HO_LABEL', ''), 'Delhi HO')

    def test_ho_codes_are_matched_case_insensitively(self):
        """One record in the directory is coded lower-case 'ho'."""
        prefix = 'HO'
        for code in ('HO0014', 'ho0231', 'Ho0999'):
            self.assertTrue(code.upper().startswith(prefix), code)
        for code in ('SL03839', 'CAND-0011', 'RK0007', 'A001'):
            self.assertFalse(code.upper().startswith(prefix), code)


class ClassifyTests(TestCase):
    """What a blank row MEANS.

    The feed returns a row for every employee on every date and carries no
    leave, week-off or holiday column. Left unclassified, every one of those
    blanks looked identical and the screen hid them -- which hid every
    absence on a screen whose job is to show them.
    """
    maxDiff = None

    def _row(self, iso, code='HO0014', location='DELHI-HO', blank=True):
        return {
            'code': code, 'date': iso, 'location': location,
            'noRecord': blank, 'flags': [] if blank else ['present'],
        }

    DELHI = {'2026-11-09': {'optional': False, 'locations': ['Delhi', 'Haryana']}}
    BENGAL = {'2026-11-11': {'optional': False, 'locations': ['ASSAM', 'WEST BENGAL']}}
    TODAY = date(2026, 11, 23)

    def test_a_working_day_with_nothing_on_it_is_absent(self):
        row = self._row('2026-11-17')          # a Tuesday, no holiday
        views._classify(row, {}, self.TODAY)
        self.assertIn('absent', row['flags'])

    def test_sunday_is_a_week_off_not_an_absence(self):
        row = self._row('2026-11-22')          # a Sunday
        views._classify(row, {}, self.TODAY)
        self.assertIn('weekOff', row['flags'])
        self.assertNotIn('absent', row['flags'])

    def test_a_future_date_is_not_an_absence(self):
        """The API answers a future range with a full set of blanks. Counting
        those as absences marks the whole company absent for next month."""
        row = self._row('2026-12-25')
        views._classify(row, {}, self.TODAY)
        self.assertIn('notYet', row['flags'])
        self.assertNotIn('absent', row['flags'])

    def test_a_holiday_at_their_location_is_not_an_absence(self):
        row = self._row('2026-11-09')
        views._classify(row, self.DELHI, self.TODAY)
        self.assertIn('holiday', row['flags'])
        self.assertNotIn('absent', row['flags'])

    def test_another_states_holiday_is_still_an_absence(self):
        """**The calendar is state-wise.** 11-11 is Assam and West Bengal
        only; marking Delhi off that day would hide a real absence."""
        row = self._row('2026-11-11', location='DELHI-HO')
        views._classify(row, self.BENGAL, self.TODAY)
        self.assertNotIn('holiday', row['flags'])
        self.assertIn('absent', row['flags'])

    def test_that_states_own_staff_do_get_it(self):
        row = self._row('2026-11-11', code='SL01234', location='WEST BENGAL')
        views._classify(row, self.BENGAL, self.TODAY)
        self.assertIn('holiday', row['flags'])

    def test_a_city_matches_its_state(self):
        """The directory says ROORKEE, the calendar says UTTARAKHAND. Roorkee
        alone is 88 people in the feed."""
        row = self._row('2026-11-09', code='RK0007', location='ROORKEE')
        views._classify(row, {'2026-11-09': {'optional': False,
                                             'locations': ['UTTARAKHAND']}}, self.TODAY)
        self.assertIn('holiday', row['flags'])

    def test_head_office_with_no_location_still_gets_delhi(self):
        """29 of the 83 HO-coded staff have a blank location. Without the
        fallback they matched no holiday and produced 30 false absences on
        every Delhi holiday."""
        row = self._row('2026-11-09', location='')
        views._classify(row, self.DELHI, self.TODAY)
        self.assertIn('holiday', row['flags'])
        self.assertNotIn('absent', row['flags'])

    def test_a_stranger_with_no_location_is_not_given_one(self):
        """Guessing a state for somebody who is not head office would be
        inventing the answer."""
        row = self._row('2026-11-09', code='SL09999', location='')
        views._classify(row, self.DELHI, self.TODAY)
        self.assertNotIn('holiday', row['flags'])
        self.assertIn('absent', row['flags'])

    def test_a_day_that_was_worked_is_never_called_absent(self):
        row = self._row('2026-11-17', blank=False)
        views._classify(row, {}, self.TODAY)
        self.assertNotIn('absent', row['flags'])
        self.assertIn('present', row['flags'])

    def test_every_blank_gets_exactly_one_meaning(self):
        meanings = {'absent', 'weekOff', 'holiday', 'notYet'}
        for iso in ('2026-11-17', '2026-11-22', '2026-11-09', '2026-12-25'):
            row = self._row(iso)
            views._classify(row, self.DELHI, self.TODAY)
            self.assertEqual(len(meanings & set(row['flags'])), 1,
                             f'{iso} -> {row["flags"]}')


class HomeOfficeScopeListTests(TestCase):

    def test_na_codes_are_inside_the_scope(self):
        """NA0001-NA0007 are in the HRMS feed, have never been in the portal
        directory, and a single 'HO' prefix dropped them from the default
        view entirely."""
        prefixes = views._home_office()['codePrefixes']
        self.assertIn('NA', prefixes)
        self.assertIn('HO', prefixes)
        for code in ('HO0239', 'NA0007'):
            self.assertTrue(any(code.startswith(p) for p in prefixes), code)
        for code in ('SL03839', 'RK0007', 'CAND-0011'):
            self.assertFalse(any(code.startswith(p) for p in prefixes), code)


# ── The day-detail feed ─────────────────────────────────────────────────────
# Every fixture below is a row seen in the live tenant on 26-11-2026, across
# 9,732 rows spanning 20-25/11 and 05-12/11 (the second window for its three
# different holidays).

# Plain working day.
D_PRESENT = {
    'EmployeeCode': 'HO0014', 'AttendanceDate': '24/11/2026',
    'PlannedShift': 'General Shift', 'PlannedShiftTime': '09:30 - 18:00',
    'InTime': '09:23', 'OutTime': '18:0', 'TotalWorkingHrs': '08:37',
    'CheckInLocation': '7/5, Block 7, New Delhi, DL, India, 110008',
    'CheckOutLocation': '-', 'Status': 'Present',
    'ReportingManagerName': 'R. Manigandan', 'ReportingManagerCode': 'HO0221',
    'ApplyType': '', 'ApplyDate': '', 'Remarks': '', 'AcceptedDate': '',
    'AcceptedManagerName': '', 'AcceptedManagerCode': '',
    'TotalWorkingDays': 1, 'LeaveBalance': 16.0, 'DeviceId': '-',
    'CheckInLongitude': '-', 'CheckInLatitude': '-',
    'CheckOutLongitude': '-', 'CheckOutLatitude': '-',
}

# Approved full-day privilege leave. Note the Status: HRMS says ABSENT.
D_LEAVE = dict(D_PRESENT, EmployeeCode='HO0210', AttendanceDate='25/11/2026',
               InTime='-', OutTime='-', TotalWorkingHrs='-', Status='Absent',
               ApplyType='SL-FD', ApplyDate='25/11/2026', Remarks='ok',
               AcceptedDate='25/11/2026', AcceptedManagerName='Amit Anand',
               CheckInLocation='-', LeaveBalance=8.0)

# Leave for the morning only -- and they came in at 12:39.
D_HALF = dict(D_PRESENT, EmployeeCode='HO0014', AttendanceDate='20/11/2026',
              InTime='12:39', OutTime='18:02', TotalWorkingHrs='05:23',
              Status='Present', ApplyType='PL-FH', Remarks='-')

# Out on company business. A working day, not a day off.
D_OD = dict(D_PRESENT, EmployeeCode='HO0064', AttendanceDate='20/11/2026',
            Status='Present', ApplyType='OD - FD', ApplyDate='20/11/2026',
            AcceptedDate='21/11/2026', CheckInLocation='-')

# Worked a Sunday that was also a holiday. One value, three tokens.
D_WORKED_OFF = dict(D_PRESENT, EmployeeCode='RK00013',
                    AttendanceDate='08/11/2026',
                    Status='Present/Week Off/Holiday')

# The device sentinel where a place should be.
D_SENTINEL = dict(D_PRESENT, CheckInLocation='2-2-2', CheckOutLocation='2-2-2')


class StatusParsingTests(TestCase):
    """`Status` is a SET joined with slashes, not a single word."""

    def test_a_single_status(self):
        self.assertEqual(hrms.status_tokens('Present'), ['present'])

    def test_a_compound_status_is_every_part_of_it(self):
        self.assertEqual(hrms.status_tokens('Present/Week Off/Holiday'),
                         ['present', 'weekOff', 'holiday'])

    def test_punch_invalid_is_its_own_token(self):
        self.assertEqual(hrms.status_tokens('Punch Invalid/Week Off'),
                         ['punchInvalid', 'weekOff'])

    def test_the_five_known_tokens_all_resolve(self):
        self.assertEqual(
            hrms.status_tokens('Present/Absent/Holiday/Week Off/Punch Invalid'),
            ['present', 'absent', 'holiday', 'weekOff', 'punchInvalid'])

    def test_an_unknown_status_is_kept_not_dropped(self):
        # The vocabulary grew once (the vendor's own summary lists statuses
        # that do not exist, and omits Punch Invalid, which does). A sixth
        # one must reach the screen rather than vanish into a blank cell.
        self.assertEqual(hrms.status_tokens('Half Day Present'),
                         ['halfDayPresent'])

    def test_a_blank_status_is_no_tokens(self):
        self.assertEqual(hrms.status_tokens(''), [])
        # `-` is the empty value across this whole API, and it is also the
        # one input that strips down to nothing -- which used to raise
        # IndexError and take the entire fetch with it.
        self.assertEqual(hrms.status_tokens('-'), [])
        self.assertEqual(hrms.status_tokens('Present/-'), ['present'])


class ApplyTypeTests(TestCase):
    """The ONLY thing that marks an absence as leave."""

    def test_a_full_day_leave(self):
        self.assertEqual(hrms.parse_apply('PL-FD'),
                         ('PL', 'Privilege leave', 'full', 'Full day'))

    def test_the_spacing_of_on_duty_is_different_and_still_parses(self):
        # Their own spelling: 'OD - FD', with spaces, unlike every other one.
        self.assertEqual(hrms.parse_apply('OD - FD'),
                         ('OD', 'On duty', 'full', 'Full day'))

    def test_first_and_second_half(self):
        self.assertEqual(hrms.parse_apply('PL-FH')[2:], ('firstHalf', 'First half'))
        self.assertEqual(hrms.parse_apply('CL-SH')[2:], ('secondHalf', 'Second half'))

    def test_an_unlabelled_code_keeps_its_code(self):
        # LD and AR are real and rare, and what they stand for is NOT
        # confirmed. A guessed expansion on somebody's leave record is worse
        # than showing the code HR will recognise.
        self.assertEqual(hrms.parse_apply('LD-FD')[:2], ('LD', 'LD'))
        self.assertEqual(hrms.parse_apply('AR')[:2], ('AR', 'AR'))

    def test_a_bare_code_with_no_span(self):
        self.assertEqual(hrms.parse_apply('AR'), ('AR', 'AR', '', ''))

    def test_the_blank_almost_every_row_carries(self):
        self.assertEqual(hrms.parse_apply(''), ('', '', '', ''))
        self.assertEqual(hrms.parse_apply('-'), ('', '', '', ''))


class PlaceTests(TestCase):

    def test_the_device_sentinel_is_not_a_place(self):
        # 4,669 rows of 9,732 carry this. Rendered as written it would read
        # as an address on half the table.
        self.assertEqual(hrms.place('2-2-2'), '')

    def test_a_real_address_survives(self):
        self.assertEqual(hrms.place('J5WF+76V, Patel Nagar, New Delhi, India,'),
                         'J5WF+76V, Patel Nagar, New Delhi, India')

    def test_the_dash_is_blank(self):
        self.assertEqual(hrms.place('-'), '')


class MalformedTimeTests(TestCase):
    """`18:0` is a real value from the feed, not a typo in a test."""

    def test_a_single_digit_minute_is_still_a_time(self):
        self.assertEqual(hrms.minutes('18:0'), 18 * 60)
        self.assertEqual(hrms.clock('18:0'), '18:00')

    def test_a_minute_past_fifty_nine_is_not_a_time(self):
        self.assertIsNone(hrms.minutes('18:73'))
        self.assertEqual(hrms.clock('18:73'), '')

    def test_an_ordinary_time_is_unchanged(self):
        self.assertEqual(hrms.clock('09:30'), '09:30')


class MergeDetailTests(TestCase):
    """Folding the second feed onto the first."""

    def _merged(self, summary, detail_raw):
        row = hrms.normalise(summary, CUTOFF)
        return hrms.merge_detail(row, hrms.parse_detail(detail_raw))

    def test_the_status_arrives_as_both_text_and_flags(self):
        row = self._merged(PUNCHED, D_PRESENT)
        self.assertEqual(row['status'], 'Present')
        self.assertIn('present', row['flags'])
        self.assertEqual(row['statusSource'], 'hrms')

    def test_the_shift_and_manager_come_across(self):
        row = self._merged(PUNCHED, D_PRESENT)
        self.assertEqual(row['shift'], 'General Shift')
        self.assertEqual(row['shiftTime'], '09:30 - 18:00')
        self.assertEqual(row['hrmsManagerCode'], 'HO0221')

    def test_hrms_calls_an_approved_leave_ABSENT(self):
        """The single most important thing to know about this feed.

        Status never says "Leave", whatever the vendor's summary claims. A
        screen reading Status alone would report somebody on approved leave
        as absent, which is how a person gets asked where they were.
        """
        row = self._merged(EMPTY, D_LEAVE)
        self.assertEqual(row['status'], 'Absent')
        self.assertIn('absent', row['flags'])
        self.assertIn('leave', row['flags'])
        self.assertEqual(row['applyLabel'], 'Sick leave')

    def test_an_explained_absence_is_not_an_unexplained_one(self):
        row = self._merged(EMPTY, D_LEAVE)
        self.assertNotIn('absentUnexplained', row['flags'])

    def test_an_absence_with_nothing_against_it_is_unexplained(self):
        row = self._merged(EMPTY, dict(D_PRESENT, Status='Absent', InTime='-',
                                       OutTime='-', TotalWorkingHrs='-'))
        self.assertIn('absentUnexplained', row['flags'])

    def test_on_duty_is_a_working_day_not_leave(self):
        row = self._merged(PUNCHED, D_OD)
        self.assertIn('onDuty', row['flags'])
        self.assertNotIn('leave', row['flags'])

    def test_a_half_day_says_which_half(self):
        row = self._merged(PUNCHED, D_HALF)
        self.assertIn('halfDay', row['flags'])
        self.assertEqual(row['applySpan'], 'firstHalf')
        self.assertEqual(row['applySpanLabel'], 'First half')

    def test_somebody_who_worked_a_holiday_is_visible(self):
        # Before this, the day was filed under "nobody was meant to be here"
        # and the punch went with it.
        row = self._merged(PUNCHED, D_WORKED_OFF)
        self.assertIn('workedOnOff', row['flags'])
        self.assertIn('present', row['flags'])
        self.assertIn('holiday', row['flags'])
        self.assertIn('weekOff', row['flags'])

    def test_punch_invalid_means_the_same_as_unswiped(self):
        row = self._merged(EMPTY, dict(D_PRESENT, Status='Punch Invalid'))
        self.assertIn('unswiped', row['flags'])

    def test_the_sentinel_place_is_dropped(self):
        row = self._merged(PUNCHED, D_SENTINEL)
        self.assertEqual(row['checkInPlace'], '')
        self.assertEqual(row['checkOutPlace'], '')

    def test_a_negative_leave_balance_is_a_number_not_a_blank(self):
        # 112 rows of 9,732 are overdrawn, down to -17.0. Reading one as
        # "no data" would quietly hide exactly the cases HR looks for.
        row = self._merged(PUNCHED, dict(D_PRESENT, LeaveBalance=-1.5))
        self.assertEqual(row['leaveBalance'], -1.5)

    def test_a_missing_detail_row_leaves_the_row_usable(self):
        row = hrms.merge_detail(hrms.normalise(PUNCHED, CUTOFF), None)
        self.assertEqual(row['statusSource'], 'derived')
        self.assertIn('present', row['flags'])

    def test_the_merge_key_is_the_code_and_the_date(self):
        index = hrms.index_detail([D_PRESENT, D_LEAVE])
        self.assertIn(('HO0014', '2026-11-24'), index)
        self.assertIn(('HO0210', '2026-11-25'), index)


class HrmsBeatsTheGuessTests(TestCase):
    """`_classify` is the fallback now. Where HRMS spoke, it stands."""

    TODAY = date(2026, 11, 23)

    def _blank_row(self, iso, flags):
        return {'code': 'HO0014', 'date': iso, 'location': 'DELHI-HO',
                'noRecord': True, 'flags': list(flags)}

    def test_a_sunday_hrms_called_a_working_day_is_not_overridden(self):
        # Saturday is a working day here and Sunday is not, but the roster
        # is HRMS's to state -- the weekday is our inference, and a guess
        # does not get to overrule a fact.
        row = self._blank_row('2026-11-22', ['absent'])
        views._classify(row, {}, self.TODAY)
        self.assertIn('absent', row['flags'])
        self.assertNotIn('weekOff', row['flags'])

    def test_the_weekday_fact_is_still_recorded(self):
        row = self._blank_row('2026-11-22', ['absent'])
        views._classify(row, {}, self.TODAY)
        self.assertIn('sunday', row['flags'])

    def test_with_no_hrms_verdict_the_old_guess_still_runs(self):
        row = self._blank_row('2026-11-17', [])
        views._classify(row, {}, self.TODAY)
        self.assertIn('absent', row['flags'])

    def test_the_fallback_path_also_marks_an_absence_unexplained(self):
        row = self._blank_row('2026-11-17', [])
        views._classify(row, {}, self.TODAY)
        self.assertIn('absentUnexplained', row['flags'])

    def test_a_future_date_is_never_absent_either_way(self):
        row = self._blank_row('2026-12-25', [])
        views._classify(row, {}, self.TODAY)
        self.assertIn('notYet', row['flags'])
        self.assertNotIn('absent', row['flags'])
