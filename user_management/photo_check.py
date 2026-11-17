"""Is the attendance photo a photo of a person doing their job?

The field attendance export (BIZOM) carries a photo taken at punch-in. Some
of them are a black frame, a thumb over the lens, a desk, or a picture of a
wall, and the row still reads Present with a tidy timestamp beside it. Those
are the ones somebody should ask about.

**Why this is on the server and not in the browser.** It was in the browser
first, and it could not work there: reading a photo's pixels back out of a
canvas needs the host serving it to send `Access-Control-Allow-Origin`, and
BIZOM's does not. The image displays perfectly in an `<img>` and is then
unreadable to any code on the page. The server has no such rule, so it
fetches the image itself and answers with numbers instead.

**What it measures, and what it does not.** Three cheap, explainable things:

    brightness   mean luma -- a black or blown-out frame
    detail       how much the picture changes from pixel to pixel -- a
                 covered lens or a blank wall has almost none
    skin         share of pixels inside a skin-tone range in YCbCr

The first two are solid: a frame with no light or no variation contains
nothing, whoever is looking. **The third is a hint and is labelled as one.**
It is a colour range, not a face detector -- it cannot tell a face from a
forearm, and coloured light, a mask, gloves or a dark room will all push it
down on a photograph of a perfectly present human being. So a low skin share
is reported as `no_person`, which the UI shows as "may not be a person --
worth a look", and never as an accusation. Anything stronger needs a real
face model, which is a different change with a real dependency behind it.

**Fetching URLs out of a sheet is a server-side request forgery hole** if it
is done naively: paste `http://127.0.0.1:8000/api/...` into the column and
the server fetches its own admin for you. Every URL goes through
`url_problem()` first -- http(s) only, and every address it resolves to must
be a public one -- and every redirect hop is checked again.
"""
import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import numpy as np
import requests
from PIL import Image

# Matches the browser copy that this replaced, so the two agree if the
# fallback ever runs: below DARK the frame is black, above BRIGHT it is
# blown out, below FLAT there is no variation in it at all.
DARK, BRIGHT, FLAT = 18.0, 242.0, 12.0

# Mean absolute neighbour difference. A real scene -- any real scene --
# changes from one pixel to the next; a lens cap does not.
MIN_EDGES = 2.0

# Share of pixels in the skin range. Deliberately low: the cost of missing a
# blank photo is that somebody is not asked about it, and the cost of a false
# one is that a person is asked to explain a photograph of themselves.
MIN_SKIN = 0.03

WORK_SIZE = 128          # enough to answer all three questions
MAX_BYTES = 8 * 1024 * 1024
TIMEOUT = 10
MAX_HOPS = 3
MAX_URLS = 300           # one day of one zone, not a whole month at once
WORKERS = 8

UA = 'APIS-Intranet/1.0 (attendance photo check)'


# ── not letting the sheet choose what the server fetches ─────────────────

def url_problem(url):
    """-> why this URL may not be fetched, or '' if it may.

    Resolving and checking every address rather than matching on the host
    name: a name under somebody else's control can point at 127.0.0.1, and
    a check that reads the string rather than the answer never sees it.
    """
    try:
        p = urlparse(url)
    except ValueError:
        return 'not a URL'
    if p.scheme not in ('http', 'https'):
        return 'only http and https are fetched'
    if not p.hostname:
        return 'no host in the URL'
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80),
                                   proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError, ValueError):
        # gaierror is the usual one, but a bad name can surface as a plain
        # OSError and that must not escape as a 500.
        return 'the host does not resolve'
    if not infos:
        return 'the host does not resolve'
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return 'the host resolves to something unusable'
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return 'the URL points inside our own network'
    return ''


def _fetch(url, session):
    """-> image bytes, or an error string. Redirects are followed by hand so
    that each hop is checked like the first one."""
    seen = url
    for _ in range(MAX_HOPS):
        problem = url_problem(seen)
        if problem:
            return problem
        try:
            resp = session.get(seen, timeout=TIMEOUT, stream=True,
                               allow_redirects=False, headers={'User-Agent': UA})
        except requests.RequestException:
            return 'could not reach the image'

        if resp.is_redirect or resp.is_permanent_redirect:
            nxt = resp.headers.get('Location')
            resp.close()
            if not nxt:
                return 'redirect with nowhere to go'
            seen = requests.compat.urljoin(seen, nxt)
            continue

        if resp.status_code != 200:
            resp.close()
            return f'the image host answered {resp.status_code}'

        # Length first where it is given, then count bytes anyway: a host is
        # free to lie about Content-Length, or omit it entirely.
        try:
            if int(resp.headers.get('Content-Length') or 0) > MAX_BYTES:
                resp.close()
                return 'the image is too large'
        except ValueError:
            pass
        buf = bytearray()
        try:
            for chunk in resp.iter_content(64 * 1024):
                buf += chunk
                if len(buf) > MAX_BYTES:
                    return 'the image is too large'
        except requests.RequestException:
            return 'the download stopped part way'
        finally:
            resp.close()
        return bytes(buf)
    return 'too many redirects'


# ── what the picture is like ─────────────────────────────────────────────

def measure(raw):
    """-> (brightness, detail, skin share) for image bytes, or an error string."""
    from io import BytesIO
    try:
        img = Image.open(BytesIO(raw))
        img.draft('RGB', (WORK_SIZE, WORK_SIZE))   # JPEG shortcut; free when it applies
        img = img.convert('RGB')
    except Exception:
        return 'not an image we can read'
    if not img.width or not img.height:
        return 'the image is empty'
    img.thumbnail((WORK_SIZE, WORK_SIZE))

    a = np.asarray(img, dtype=np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    # Rec. 601 luma, which is also what the YCbCr skin range below assumes.
    y = 0.299 * r + 0.587 * g + 0.114 * b

    cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b

    mean = float(y.mean())

    # Variation and detail are measured across colour as well as brightness,
    # and the strongest channel wins.
    #
    # Luma alone is not enough. Two different colours can share a brightness
    # almost exactly -- a blue shutter against green foliage comes out at 83
    # and 86 -- so a picture full of things reads as a flat grey card and
    # gets called featureless. A frame with nothing in it has nothing in any
    # channel, which is the thing actually being tested.
    def spread(ch):
        return float(ch.std())

    def detail(ch):
        # Both directions, averaged, so a frame that varies only across (a
        # horizon, a doorframe) is not mistaken for an empty one.
        dx = np.abs(np.diff(ch, axis=1)).mean() if ch.shape[1] > 1 else 0.0
        dy = np.abs(np.diff(ch, axis=0)).mean() if ch.shape[0] > 1 else 0.0
        return float((dx + dy) / 2)

    sd = max(spread(y), spread(cb), spread(cr))
    edges = max(detail(y), detail(cb), detail(cr))

    # Chai & Ngan's Cb/Cr window, with a floor on luma so that near-black
    # pixels -- whose chroma is noise -- cannot be counted as skin.
    skin = float(((cb >= 77) & (cb <= 127) & (cr >= 133) & (cr <= 173) & (y > 60)).mean())

    return mean, sd, edges, skin


def verdict_for(raw):
    """-> the dict that goes back to the browser for one image."""
    got = measure(raw)
    if isinstance(got, str):
        return {'verdict': 'unreadable', 'reason': got}
    mean, sd, edges, skin = got
    stats = {'mean': round(mean, 1), 'sd': round(sd, 1),
             'edges': round(edges, 2), 'skin': round(skin, 3)}

    if mean < DARK:
        return {'verdict': 'blank', 'reason': 'almost completely black', **stats}
    if mean > BRIGHT:
        return {'verdict': 'blank', 'reason': 'almost completely white', **stats}
    if sd < FLAT or edges < MIN_EDGES:
        return {'verdict': 'blank', 'reason': 'no detail in the frame', **stats}
    # Not 'blank': there IS a picture here, it just does not look like it has
    # anybody in it. Different question, different answer, different wording.
    if skin < MIN_SKIN:
        return {'verdict': 'no_person', 'reason': 'no skin tone in the frame', **stats}
    return {'verdict': 'ok', 'reason': 'a photo with a person in it', **stats}


def check_one(url, session):
    problem = url_problem(url)
    if problem:
        return {'verdict': 'unreadable', 'reason': problem}
    raw = _fetch(url, session)
    if isinstance(raw, str):
        return {'verdict': 'unreadable', 'reason': raw}
    return verdict_for(raw)


def check_urls(urls):
    """-> {url: verdict dict} for up to MAX_URLS images, fetched in parallel.

    One requests.Session per worker rather than one shared: a Session is not
    documented as thread-safe, and the connection pooling is per host anyway,
    which is where the win is.
    """
    wanted, seen = [], set()
    for u in urls:
        u = (u or '').strip()
        if u and u not in seen:
            seen.add(u)
            wanted.append(u)
        if len(wanted) >= MAX_URLS:
            break
    if not wanted:
        return {}

    def run(batch):
        with requests.Session() as s:
            return [(u, check_one(u, s)) for u in batch]

    n = min(WORKERS, len(wanted))
    batches = [wanted[i::n] for i in range(n)]
    out = {}
    with ThreadPoolExecutor(max_workers=n) as pool:
        for part in pool.map(run, batches):
            out.update(dict(part))
    return out
