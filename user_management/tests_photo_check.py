"""The attendance photo check.

Two things it must not get wrong, and they pull in opposite directions.

Calling a real photograph blank means somebody is asked to explain a picture
of themselves doing their job. Calling a black frame fine means the check
buys nothing. Most of what is below is the first kind, because that is the
one with a person on the other end of it.

The third thing is `url_problem`: this endpoint fetches whatever URL is in
the spreadsheet, and a column anyone can edit is not a safe place to take
instructions about what the server should go and read.
"""
from io import BytesIO
from unittest.mock import patch

import numpy as np
from django.test import TestCase
from PIL import Image

from .photo_check import (MAX_URLS, check_urls, measure, url_problem, verdict_for)


def png(arr):
    """A PNG of the given HxWx3 array."""
    buf = BytesIO()
    Image.fromarray(arr.astype('uint8'), 'RGB').save(buf, 'PNG')
    return buf.getvalue()


def flat(rgb, size=160):
    return png(np.tile(np.array(rgb, dtype='uint8'), (size, size, 1)))


def noisy(rgb, size=160, spread=8, seed=1):
    """A solid colour with enough grain to count as a real picture."""
    rng = np.random.default_rng(seed)
    a = np.tile(np.array(rgb, dtype='int16'), (size, size, 1))
    return png(np.clip(a + rng.integers(-spread, spread + 1, a.shape), 0, 255))


def half_and_half(top, bottom, size=160, seed=2):
    """Two colours and some grain -- the shape of an ordinary photograph."""
    rng = np.random.default_rng(seed)
    a = np.zeros((size, size, 3), dtype='int16')
    a[: size // 2] = top
    a[size // 2:] = bottom
    return png(np.clip(a + rng.integers(-8, 9, a.shape), 0, 255))


SKIN = (205, 155, 125)      # a face under ordinary light
SHIRT = (40, 80, 140)       # something that is not skin


class APhotoWithNothingInIt(TestCase):

    def test_a_black_frame_is_blank(self):
        """The one in the live file: punched in with the lens covered."""
        v = verdict_for(flat((0, 0, 0)))
        self.assertEqual(v['verdict'], 'blank')
        self.assertIn('black', v['reason'])

    def test_a_nearly_black_frame_is_blank(self):
        """Not pure zero -- a real covered lens leaks a little light."""
        self.assertEqual(verdict_for(noisy((6, 6, 7), spread=3))['verdict'], 'blank')

    def test_a_blown_out_frame_is_blank(self):
        """Flash straight into a wall."""
        v = verdict_for(flat((252, 252, 252)))
        self.assertEqual(v['verdict'], 'blank')
        self.assertIn('white', v['reason'])

    def test_a_featureless_wall_is_blank(self):
        """Mid-grey, properly exposed, and containing nothing at all. This
        is the case brightness alone cannot catch."""
        v = verdict_for(flat((128, 128, 128)))
        self.assertEqual(v['verdict'], 'blank')
        self.assertIn('detail', v['reason'])


class APhotoWithSomethingInIt(TestCase):

    def test_a_person_passes(self):
        self.assertEqual(verdict_for(half_and_half(SKIN, SHIRT))['verdict'], 'ok')

    def test_a_dim_photo_of_a_person_is_not_called_blank(self):
        """Indoors, evening, no flash. Darker than ideal is not empty, and
        treating it as empty is how a real employee gets asked to explain a
        photograph of themselves."""
        dim = tuple(int(c * 0.55) for c in SKIN)
        v = verdict_for(half_and_half(dim, (20, 40, 70)))
        self.assertNotEqual(v['verdict'], 'blank', v)

    def test_a_scene_with_no_person_is_flagged_separately(self):
        """A photo of a shop front or a desk: there IS a picture here, it
        just has nobody in it. That is a different answer from 'blank', and
        the wording has to keep them apart."""
        v = verdict_for(half_and_half((40, 90, 160), (30, 120, 60)))
        self.assertEqual(v['verdict'], 'no_person', v)
        self.assertIn('skin', v['reason'])

    def test_colour_counts_as_detail_even_when_brightness_does_not(self):
        """Blue shutter against green foliage: luma 83 against 86, which to
        a brightness-only check is one flat grey card. Measuring the colour
        channels too is what stops a picture full of things being called
        empty."""
        v = verdict_for(half_and_half((40, 90, 160), (30, 120, 60)))
        self.assertNotEqual(v['verdict'], 'blank', v)

    def test_the_numbers_come_back_for_a_human_to_judge(self):
        v = verdict_for(half_and_half(SKIN, SHIRT))
        for k in ('mean', 'sd', 'edges', 'skin'):
            self.assertIn(k, v)

    def test_chroma_noise_in_a_black_frame_is_not_counted_as_skin(self):
        """Near-black pixels have meaningless colour. Without the luma floor
        a black frame scores skin and comes back as a person."""
        got = measure(noisy((4, 3, 5), spread=4))
        self.assertLess(got[3], 0.03, 'a black frame should hold no skin')


class WhenTheImageCannotBeRead(TestCase):

    def test_bytes_that_are_not_an_image(self):
        v = verdict_for(b'this is not a picture')
        self.assertEqual(v['verdict'], 'unreadable')

    def test_an_empty_body(self):
        self.assertEqual(verdict_for(b'')['verdict'], 'unreadable')


class WhatTheServerMayBeToldToFetch(TestCase):
    """The URL comes out of a spreadsheet column. It is not a request we
    chose to make, so it is not trusted to be outward-facing."""

    def test_only_http_and_https(self):
        for bad in ('file:///etc/passwd', 'ftp://host/x.jpg', 'gopher://h/1'):
            self.assertIn('http', url_problem(bad), bad)

    def test_a_url_with_no_host(self):
        self.assertTrue(url_problem('http:///nowhere.jpg'))

    def test_our_own_machine_is_refused(self):
        """`http://127.0.0.1:8000/api/...` in the column would otherwise
        have the server fetch its own API and hand back the answer."""
        for bad in ('http://127.0.0.1:8000/api/accounts/',
                    'http://localhost/admin/',
                    'http://[::1]/'):
            self.assertIn('our own network', url_problem(bad), bad)

    def test_the_private_network_is_refused(self):
        for bad in ('http://10.0.0.5/x.jpg', 'http://192.168.1.1/x.jpg',
                    'http://172.16.0.9/x.jpg'):
            self.assertIn('our own network', url_problem(bad), bad)

    def test_the_cloud_metadata_address_is_refused(self):
        """169.254.169.254 hands out machine credentials on most hosts."""
        self.assertIn('our own network',
                      url_problem('http://169.254.169.254/latest/meta-data/'))

    def test_a_name_that_resolves_inward_is_refused(self):
        """The check is on the address, not the spelling. A host somebody
        else controls can point at 127.0.0.1, and a string check never sees
        it happen."""
        with patch('user_management.photo_check.socket.getaddrinfo',
                   return_value=[(2, 1, 6, '', ('127.0.0.1', 80))]):
            self.assertIn('our own network', url_problem('http://images.example.com/a.jpg'))

    def test_an_ordinary_public_image_is_allowed(self):
        with patch('user_management.photo_check.socket.getaddrinfo',
                   return_value=[(2, 1, 6, '', ('93.184.216.34', 443))]):
            self.assertEqual(url_problem('https://images.example.com/a.jpg'), '')

    def test_a_host_that_does_not_exist(self):
        with patch('user_management.photo_check.socket.getaddrinfo',
                   side_effect=OSError('nope')):
            self.assertIn('does not resolve', url_problem('https://nope.invalid/a.jpg'))


class TheBatch(TestCase):

    def test_the_same_photo_is_only_fetched_once(self):
        """One photo can sit on several rows, and the day is slow enough
        without downloading it three times."""
        seen = []

        def fake(url, session):
            seen.append(url)
            return {'verdict': 'ok', 'reason': 'x'}

        with patch('user_management.photo_check.check_one', side_effect=fake):
            out = check_urls(['http://a/1.jpg', 'http://a/1.jpg', 'http://a/2.jpg'])
        self.assertEqual(sorted(seen), ['http://a/1.jpg', 'http://a/2.jpg'])
        self.assertEqual(len(out), 2)

    def test_blanks_and_nothings_are_dropped(self):
        self.assertEqual(check_urls(['', '   ', None]), {})

    def test_it_stops_at_the_cap(self):
        with patch('user_management.photo_check.check_one',
                   return_value={'verdict': 'ok', 'reason': 'x'}):
            out = check_urls([f'http://a/{i}.jpg' for i in range(MAX_URLS + 50)])
        self.assertEqual(len(out), MAX_URLS)
