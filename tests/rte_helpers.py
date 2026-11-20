"""Shared helpers for the RTÉ plugin test suite.

Nothing here imports the plugin. yt-dlp runs as a separate program, with plugins
switched off except for the checkout this file sits in. Expected values come from
RTÉ's raw data, fetched with plain curl (default user agent).
"""
import atexit
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
import zipfile

try:
    from zoneinfo import ZoneInfo
    DUBLIN = ZoneInfo('Europe/Dublin')
except Exception:  # no tz database
    DUBLIN = None

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
# --plugin-dirs takes a folder that holds plugin packages, and yt-dlp loads every package in it.
# The checkout's own parent may hold other plugins (e.g. ~/.yt-dlp/plugins), so yt-dlp is given a
# private temporary folder holding only a link to this checkout.
PLUGIN_PARENT = tempfile.mkdtemp(prefix='rte-tests-plugins-')
_PLUGIN_LINK = os.path.join(PLUGIN_PARENT, os.path.basename(REPO_ROOT))
os.symlink(REPO_ROOT, _PLUGIN_LINK, target_is_directory=True)


def _remove_plugin_link():
    # unlink the symlink itself (never follows it into the checkout), then the empty folder
    if os.path.islink(_PLUGIN_LINK):
        os.unlink(_PLUGIN_LINK)
    try:
        os.rmdir(PLUGIN_PARENT)
    except OSError:
        pass


atexit.register(_remove_plugin_link)
YTDLP = os.environ.get('YTDLP', '/usr/local/bin/yt-dlp')
IN_IRELAND = os.environ.get('RTE_TESTS_IN_IRELAND') == '1'
TIMEOUT = int(os.environ.get('RTE_TESTS_TIMEOUT', '600'))

FEED = 'https://feed.entertainment.tv.theplatform.eu/f/1uC-gC/prd-webplayer-programmes/'
PID_LOOKUP = 'https://feed.theplatform.eu/f/1uC-gC/rte-prd-media-pid-lookup'
AVGEN = 'https://www.rte.ie/rteavgen/getplaylist/'
STATIONS_JSON = 'https://www.rte.ie/radio/live_stations/json'
GUID_PREFIX = 'epic-mpx-manual-'

# ---------------------------------------------------------------------------------------------
# Request accounting (printed at the end of the run)

STATS = {'curl': 0, 'ytdlp_runs': 0, 'ytdlp_requests': {}}
_COUNTER_DIR = tempfile.mkdtemp(prefix='rte-tests-')


def _report_stats():
    hosts = STATS['ytdlp_requests']
    total = sum(hosts.values())
    detail = ', '.join(f'{h} {n}' for h, n in sorted(hosts.items(), key=lambda x: -x[1]))
    sys.stderr.write(
        f'\n[rte tests] curl requests (expected values): {STATS["curl"]}; yt-dlp runs: {STATS["ytdlp_runs"]}; '
        f'requests made by yt-dlp: {total if _CAN_WRAP else "not counted"}'
        + (f' ({detail})' if detail else '') + '\n')
    shutil.rmtree(_COUNTER_DIR, ignore_errors=True)


atexit.register(_report_stats)

# ---------------------------------------------------------------------------------------------
# yt-dlp

_CAN_WRAP = os.path.isfile(YTDLP) and zipfile.is_zipfile(YTDLP)

# Runs yt-dlp's own main() from the zipapp, unchanged, but counts requests per host and can
# inject faults (HTTP status or timeout) for URLs matching a regex. Used for every run when
# $YTDLP is a zipapp (the normal pip-less install); otherwise yt-dlp is executed directly.
_WRAPPER = r'''
import io, json, os, re, sys, urllib.parse, urllib.request
sys.path.insert(0, os.environ['RTE_TEST_YTDLP'])
sys.argv[0] = 'yt-dlp'
import yt_dlp
from yt_dlp.YoutubeDL import YoutubeDL
from yt_dlp.networking.common import Response
from yt_dlp.networking.exceptions import HTTPError, TransportError
faults = json.loads(os.environ.get('RTE_TEST_FAULTS') or '[]')
counts = {}
counter = os.environ.get('RTE_TEST_COUNTER')
def _save():
    if counter:
        with open(counter, 'w') as f:
            json.dump({'counts': counts, 'faults_left': faults}, f)
import atexit; atexit.register(_save)
_orig = YoutubeDL.urlopen
def urlopen(self, req):
    url = req if isinstance(req, str) else (getattr(req, 'url', None) or req.get_full_url())
    host = urllib.parse.urlparse(url).hostname or '?'
    counts[host] = counts.get(host, 0) + 1
    for f in faults:
        if f.get('times', 1) != 0 and re.search(f['match'], url):
            if f.get('times', 1) > 0:
                f['times'] = f.get('times', 1) - 1
            f['hits'] = f.get('hits', 0) + 1
            if f['status'] == 'timeout':
                raise TransportError('injected timeout (test fault)')
            raise HTTPError(Response(io.BytesIO(b'injected by test'), url, {}, status=f['status'], reason='Injected'))
    return _orig(self, req)
YoutubeDL.urlopen = urlopen
yt_dlp.main(sys.argv[1:])
'''


class YtdlpResult:
    def __init__(self, args, proc, faults_left=None):
        self.args = args
        self.returncode = proc.returncode
        self.stdout = proc.stdout
        self.stderr = proc.stderr
        self.faults_left = faults_left

    @property
    def output(self):
        return self.stdout + '\n' + self.stderr

    def json(self):
        """The single JSON document printed by -J / -j (first line for -j)."""
        # (--print-traffic writes request/response lines to stdout as well)
        for line in self.stdout.splitlines():
            if line.startswith('{'):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        return None

    def json_lines(self):
        return [json.loads(line) for line in self.stdout.splitlines() if line.strip().startswith('{')]

    def describe(self):
        tail = '\n'.join(self.stderr.strip().splitlines()[-15:])
        return f'yt-dlp {" ".join(self.args)}\n  exit code {self.returncode}\n  stderr (last lines):\n{tail}'


def ytdlp(*args, plugins=True, faults=None, timeout=TIMEOUT, cwd=None):
    """Run yt-dlp with --ignore-config and only this checkout's plugin (or no plugins)."""
    base = ['--ignore-config', '--no-cache-dir', '--no-plugin-dirs']
    if plugins:
        base += ['--plugin-dirs', PLUGIN_PARENT]
    args = [*base, *args]
    env = dict(os.environ)
    env.pop('YTDLP_NO_PLUGINS', None)
    counter = None
    if _CAN_WRAP:
        fd, counter = tempfile.mkstemp(dir=_COUNTER_DIR, suffix='.json')
        os.close(fd)
        env.update(RTE_TEST_YTDLP=YTDLP, RTE_TEST_COUNTER=counter, RTE_TEST_FAULTS=json.dumps(faults or []))
        cmd = [sys.executable, '-c', _WRAPPER, *args]
    else:
        if faults:
            raise unittest.SkipTest('fault injection needs $YTDLP to be the yt-dlp zipapp')
        cmd = [YTDLP, *args]
    STATS['ytdlp_runs'] += 1
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace',
                              timeout=timeout, env=env, cwd=cwd or _COUNTER_DIR)
    except subprocess.TimeoutExpired as e:
        raise AssertionError(f'yt-dlp timed out after {timeout}s: {" ".join(args)}') from e
    faults_left = None
    if counter:
        try:
            with open(counter) as f:
                data = json.load(f)
            for host, n in data.get('counts', {}).items():
                STATS['ytdlp_requests'][host] = STATS['ytdlp_requests'].get(host, 0) + n
            faults_left = data.get('faults_left')
        except (OSError, ValueError):
            pass
        os.unlink(counter)
    return YtdlpResult(args, proc, faults_left)


def info(url, *extra, **kw):
    """Full extraction (-j, no download). Returns (info dict or None, YtdlpResult)."""
    res = ytdlp('-j', '--no-download', *extra, url, **kw)
    return res.json(), res


def flat(url, *extra, **kw):
    """Flat playlist (-J --flat-playlist). Returns (playlist dict or None, YtdlpResult)."""
    res = ytdlp('-J', '--flat-playlist', *extra, url, **kw)
    return res.json(), res


# ---------------------------------------------------------------------------------------------
# RTÉ raw data (plain curl, default user agent; cached for the run)

_cache = {}


def curl(url, query=None, *, follow=True):
    """(HTTP status, body text). Plain curl with its default user agent."""
    if query:
        url = f'{url}{"&" if "?" in url else "?"}{urllib.parse.urlencode(query)}'
    if url in _cache:
        return _cache[url]
    STATS['curl'] += 1
    with tempfile.NamedTemporaryFile(dir=_COUNTER_DIR, delete=False) as tmp:
        path = tmp.name
    try:
        cmd = ['curl', '-s', '-o', path, '-w', '%{http_code}', '--max-time', '120', '--retry', '2']
        if follow:
            cmd.append('-L')
        proc = subprocess.run([*cmd, url], capture_output=True, text=True, timeout=180)
        with open(path, encoding='utf-8', errors='replace') as f:
            body = f.read()
    finally:
        os.unlink(path)
    status = int(proc.stdout.strip() or 0)
    _cache[url] = status, body
    return status, body


def curl_json(url, query=None):
    status, body = curl(url, query)
    try:
        return status, json.loads(body)
    except ValueError:
        return status, None


def feed(query, fields=None):
    q = {'byProgramType': 'clip', 'sort': 'rte$broadcastDate|desc', **query}
    if fields:
        q['fields'] = fields
    status, data = curl_json(FEED, q)
    return status, data


def feed_entries(query, fields=None):
    status, data = feed(query, fields)
    if status != 200 or not isinstance(data, dict):
        raise AssertionError(f'RTÉ feed query {query} returned HTTP {status}')
    return data.get('entries') or []


def avgen(item_id):
    """RTÉ's older lookup (getplaylist): (status, show dict or None, raw JSON)."""
    status, data = curl_json(AVGEN, {'type': 'web', 'format': 'json', 'id': item_id})
    show = (data or {}).get('shows', [None])[0] if isinstance(data, dict) and data.get('shows') else None
    return status, show, data


def avgen_medium(show):
    return show.get('medium') or ((show.get('media:group') or [{}])[0] or {}).get('medium')


def programme_page(station, programme, kind='episodes', page=1):
    """RTÉ's episodes/json or clips/json page: (status, list of items or None)."""
    status, data = curl_json(f'https://www.rte.ie/radio/{station}/{programme}/{kind}/json/', {'page': page})
    items = data.get('programmes') if isinstance(data, dict) else None
    return status, items


def stations():
    """RTÉ's station list: {id: (slug, name)}."""
    status, data = curl_json(STATIONS_JSON)
    if status != 200 or not data:
        raise AssertionError(f'RTÉ live_stations/json returned HTTP {status}')
    return {int(s['id']): (s['slug'], s['name']) for s in data['stations']}


def page_html(url):
    status, body = curl(url)
    return status, body


def og(html, prop):
    m = re.search(rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']*)', html)
    if not m:
        m = re.search(rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']{re.escape(prop)}["\']', html)
    import html as _html
    return _html.unescape(m.group(1)) if m else None


# ---------------------------------------------------------------------------------------------
# Expected-value helpers (written from RTÉ's documented formats, not from the plugin)

def ts_from_rte(value):
    """UTC timestamp for an RTÉ date string: 'Z'/offset = as stated; no zone = Irish local time."""
    if not value or not isinstance(value, str) or value.startswith('0001'):
        return None
    value = value.strip()
    if value.endswith('Z'):
        d = dt.datetime.fromisoformat(value[:-1]).replace(tzinfo=dt.timezone.utc)
    elif re.search(r'[+-]\d{2}:?\d{2}$', value):
        d = dt.datetime.fromisoformat(value)
    else:
        if DUBLIN is None:
            raise unittest.SkipTest('no tz database for Europe/Dublin')
        d = dt.datetime.fromisoformat(value).replace(tzinfo=DUBLIN)
    ts = int(d.timestamp())
    return ts if ts > 0 else None


def utc_date(ts):
    return None if ts is None else dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime('%Y%m%d')


def clean(title):
    """RTÉ title with HTML entities decoded, NUL removed and whitespace collapsed."""
    import html as _html
    if title is None:
        return None
    return re.sub(r'\s+', ' ', _html.unescape(str(title)).replace('\x00', ' ')).strip() or None


def norm_id(item_id):
    s = str(item_id).strip().lower()
    return str(int(s)) if s.isdigit() else s


def expected_media_type(show):
    """The media_type rule (documented in RteAvgenIE in the plugin), applied to RTÉ's getplaylist data."""
    station_ids = set(stations())
    if avgen_medium(show) != 'audio' or int(show.get('channelid') or -1) not in station_ids:
        return 'clip'
    ep = show.get('episode_id')
    if ep and str(ep).lower() != str(show.get('itemid')).lower():
        return 'clip'
    if show.get('form') in ('long', 'short'):
        return 'episode' if show['form'] == 'long' else 'clip'
    if not str(show.get('itemid')).isdigit():
        return 'episode'
    return None  # not decided by the rule


def guid_to_id(guid):
    return guid[len(GUID_PREFIX):] if guid.startswith(GUID_PREFIX) else guid


class RteTestCase(unittest.TestCase):
    maxDiff = 4000

    def assertRte(self, field, expected, actual, source, url=None):
        """Equality with a message saying what RTÉ returned and what the plugin returned."""
        if expected != actual:
            self.fail(f'{field}: RTÉ ({source}) gives {expected!r}; the plugin returned {actual!r}'
                      + (f'\n  URL: {url}' if url else ''))

    def assertClose(self, field, expected, actual, tol, source, url=None):
        if expected is None or actual is None or abs(expected - actual) > tol:
            self.fail(f'{field}: RTÉ ({source}) gives {expected!r}; the plugin returned {actual!r} '
                      f'(tolerance {tol})' + (f'\n  URL: {url}' if url else ''))

    def assertFaultHit(self, res):
        hits = [f.get('hits', 0) for f in res.faults_left or []]
        self.assertTrue(hits and all(hits), f'injected fault never matched a request (test is vacuous): {res.faults_left}')

    def assertOk(self, res, what=''):
        if res.returncode != 0:
            self.fail(f'{what}yt-dlp failed\n{res.describe()}')

    def assertNoTraceback(self, res):
        """No crash: no traceback (outside -v runs, which print one for every error) and no
        'please report this issue' (yt-dlp's marker for unexpected extractor errors)."""
        if '-v' not in res.args and '--verbose' not in res.args:
            self.assertNotIn('Traceback (most recent call last)', res.output, f'Python traceback\n{res.describe()}')
        self.assertNotIn('please report this issue', res.output, f'unexpected (non-expected) error\n{res.describe()}')

    def assertErrorContains(self, res, text):
        self.assertNotEqual(res.returncode, 0, f'expected an error containing {text!r}\n{res.describe()}')
        self.assertIn(text, res.stderr, f'expected an error containing {text!r}\n{res.describe()}')
        self.assertNoTraceback(res)

    def assertUnsupported(self, res):
        self.assertNotEqual(res.returncode, 0, f'expected "Unsupported URL"\n{res.describe()}')
        self.assertIn('Unsupported URL', res.stderr, f'expected "Unsupported URL"\n{res.describe()}')

    def skipIfRte(self, cond, why):
        if cond:
            self.skipTest(f'RTÉ data changed: {why}')
