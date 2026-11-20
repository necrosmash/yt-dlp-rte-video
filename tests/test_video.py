"""rte.ie/video: clips (rte:video), categories and the "all" listing (rte:video:category).

Expected values come from RTÉ's thePlatform feed (curl)."""
import os
import re
import tempfile
import unittest

from rte_helpers import (
    GUID_PREFIX, IN_IRELAND, RteTestCase, clean, curl, feed, feed_entries, flat, guid_to_id, info, og,
    utc_date, ytdlp,
)

FIELDS = ','.join((
    'guid', 'title', 'plprogram$runtime', 'rte$broadcastDate', 'rte$licenceGeos', 'plprogram$programType',
    'plprogramavailability$media.plmedia$publicUrl',
    'plprogramavailability$media.media$content.plfile$isProtected',
))
NEWS_RTE_FILTER = r'webpage_url ~= (?i)^https://www\.rte\.ie/video/id/\d+ & !is_live & !live'
WEBPAGE_RE = r'^https://www\.rte\.ie/video/id/\d+'


def _media(entry):
    return (entry.get('plprogramavailability$media') or [{}])[0] or {}


def _protected(entry):
    files = _media(entry).get('media$content') or []
    return not files or any(f.get('plfile$isProtected', True) for f in files)


def _pid(entry):
    url = _media(entry).get('plmedia$publicUrl') or ''
    return url.rsplit('/', 1)[1] if '/media/' in url else None


def _expected(entry):
    runtime = entry.get('plprogram$runtime')
    ts = int(entry['rte$broadcastDate']) // 1000 if entry.get('rte$broadcastDate') else None
    return {
        'id': guid_to_id(entry['guid']),
        'title': clean(entry.get('title')),
        'duration': float(runtime) if runtime else None,  # 0 = unknown -> absent
        'timestamp': ts,
        'upload_date': utc_date(ts),
        'rte_licence': entry.get('rte$licenceGeos'),
    }


def _first_playable(entries):
    for e in entries:
        if e['guid'].startswith(GUID_PREFIX) and e.get('rte$licenceGeos') == 'WW' and not _protected(e):
            return e
    return None


class TestVideoClip(RteTestCase):
    @classmethod
    def setUpClass(cls):
        cls.entry = _first_playable(feed_entries({'range': '1-20', 'byTags': 'epic:News'}, FIELDS))
        if not cls.entry:
            raise unittest.SkipTest('RTÉ data changed: no playable WW item among the newest 20 News clips')
        cls.exp = _expected(cls.entry)
        cls.url = f'https://www.rte.ie/video/id/{cls.exp["id"]}/'
        cls.info, cls.res = info(cls.url)

    def test_metadata_matches_feed(self):
        """An rte.ie/video clip's metadata matches RTÉ's video feed."""
        self.assertOk(self.res)
        i = self.info
        src = f'feed byGuid {self.entry["guid"]}'
        self.assertRte('extractor_key', 'RteVideo', i.get('extractor_key'), 'expected extractor')
        for key in ('id', 'title', 'timestamp', 'upload_date', 'rte_licence'):
            self.assertRte(key, self.exp[key], i.get(key), src, self.url)
        self.assertClose('duration', self.exp['duration'], i.get('duration'), 0.01, src, self.url)
        self.assertEqual(i.get('display_id'), self.entry['guid'])
        self.assertEqual(i.get('title'), (i.get('title') or '').strip(), 'title has leading/trailing spaces')

    def test_uploader_channel_and_filter_fields(self):
        """Fields that scheduled downloads filter and name files by (uploader, webpage_url, live status) are set."""
        i = self.info
        self.assertRte('uploader', 'RTÉ', i.get('uploader'), 'documented: rte.ie/video uploader')
        self.assertRte('channel', 'RTÉ', i.get('channel'), 'documented: rte.ie/video channel')
        self.assertRegex(i.get('webpage_url') or '', WEBPAGE_RE)
        self.assertFalse(i.get('is_live'), 'rte.ie/video clip marked live')
        self.assertIn(i.get('live_status') or 'not_live', ('not_live', None))
        self.assertIn(i.get('media_type'), ('clip', self.entry.get('plprogram$programType')))
        self.assertTrue(i.get('formats'), 'no formats')
        self.assertEqual(i.get('ext'), 'mp4')

    def test_dustin_letter_id_same_archive_key(self):
        """A clip and its embed get one archive key."""
        pid = _pid(self.entry)
        if not pid:
            self.skipTest('RTÉ data changed: no media pid in the feed entry')
        d, res = info(f'https://www.rte.ie/media-embed/dustin/?id={pid}')
        self.assertOk(res)
        self.assertRte('archive key', f'RteVideo {self.exp["id"]}',
                       f'{d.get("extractor_key")} {d.get("id")}', 'dustin pid -> same feed guid')

    def test_unknown_id_clean_error(self):
        """An unknown video ID gives a clean error."""
        res = ytdlp('--simulate', 'https://www.rte.ie/video/id/1/')
        self.assertErrorContains(res, 'Video not found')


class TestVideoErrors(RteTestCase):
    @classmethod
    def setUpClass(cls):
        # 300 newest items with few fields: protected RTÉ Player items and geo-limited items live here
        cls.entries = feed_entries({'range': '1-300'}, 'guid,rte$licenceGeos,plprogramavailability$media.plmedia$publicUrl,'
                                   'plprogramavailability$media.media$content.plfile$isProtected')

    def test_drm_error(self):
        """DRM-protected items stop with yt-dlp's standard DRM error (no attempt to get around it)."""
        prot = next((e for e in self.entries if _protected(e) and _pid(e)), None)
        if not prot:
            self.skipTest('RTÉ data changed: no DRM-protected item among the newest 300')
        res = ytdlp('--simulate', f'https://www.rte.ie/media-embed/dustin/?id={_pid(prot)}')
        self.assertErrorContains(res, 'This video is DRM protected')
        # non-epic feed IDs keep their full form (e.g. PI000018974)
        self.assertIn(f'{guid_to_id(prot["guid"])}: This video is DRM protected', res.stderr)

    def test_id_guard_all_digit_non_epic(self):
        """Feed IDs that look like rte.ie/video numbers get a prefix, so two items can never share an archive key."""
        digits = [e for e in self.entries if not e['guid'].startswith(GUID_PREFIX) and e['guid'].isdigit()]
        if not digits:
            self.skipTest('no all-digit non-epic guid among the newest 300 feed items; '
                          'the ID prefix guard cannot be exercised end to end today')
        e = digits[0]
        d, res = info(f'https://www.rte.ie/media-embed/dustin/?id={_pid(e)}', '--ignore-no-formats-error')
        self.assertEqual((d or {}).get('id'), f'mpx-{e["guid"]}', res.describe())

    @unittest.skipIf(IN_IRELAND, 'geo test assumes a location outside Ireland')
    def test_geo_error_and_metadata(self):
        """Ireland-only items say so, and still give metadata with --ignore-no-formats-error."""
        geo = next((e for e in feed_entries(
            {'range': '1-10', 'byCustomValue': '{licenceGeos}{ROI}'}, FIELDS)
            if e['guid'].startswith(GUID_PREFIX) and not _protected(e)), None)
        if not geo:
            self.skipTest('RTÉ data changed: no ROI-licensed unprotected item')
        url = f'https://www.rte.ie/video/id/{guid_to_id(geo["guid"])}/'
        res = ytdlp('--simulate', url)
        self.assertErrorContains(res, 'This video is only available in Ireland')
        d, res2 = info(url, '--ignore-no-formats-error')
        self.assertOk(res2, 'with --ignore-no-formats-error: ')
        self.assertIn('This video is only available in Ireland', res2.stderr)
        exp = _expected(geo)
        for key in ('id', 'title', 'timestamp', 'rte_licence'):
            self.assertRte(key, exp[key], d.get(key), 'feed (ROI item)', url)
        self.assertRte('uploader', 'RTÉ', d.get('uploader'), 'documented behaviour')

    def test_duration_zero_is_absent(self):
        """RTÉ gives some old items a duration of 0; that means unknown, not zero seconds."""
        old = feed_entries({'range': '1-10', 'byTags': 'epic:Lifestyle', 'sort': 'rte$broadcastDate'}, FIELDS)
        zero = next((e for e in old if e['guid'].startswith(GUID_PREFIX)
                     and not float(e.get('plprogram$runtime') or 0) and not _protected(e)), None)
        if not zero:
            self.skipTest('RTÉ data changed: no runtime-0 item among the oldest Lifestyle clips')
        url = f'https://www.rte.ie/video/id/{guid_to_id(zero["guid"])}/'
        d, res = info(url, '--ignore-no-formats-error')
        self.assertTrue(d, res.describe())
        self.assertIsNone(d.get('duration'), f'feed runtime is 0 (unknown) but the plugin gave duration {d.get("duration")}')


class TestCategory(RteTestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = [e for e in feed_entries({'range': '1-100', 'byTags': 'epic:News'}, FIELDS)
                   if e['guid'].startswith(GUID_PREFIX)]
        if len(cls.raw) < 20:
            raise unittest.SkipTest('RTÉ data changed: fewer than 20 News clips')
        cls.pl, cls.res = flat('https://www.rte.ie/video/category/NEWS/', '-I', '1:20')

    def test_playlist_fields(self):
        """Category playlist id, title, thumbnail, uploader and channel."""
        self.assertOk(self.res)
        pl = self.pl
        self.assertRte('playlist id', 'news', pl.get('id'), 'URL category, lowercased')
        self.assertRte('playlist title', 'RTÉ video: News', pl.get('title'), 'documented: capitalised regardless of URL case')
        self.assertRte('playlist uploader', 'RTÉ', pl.get('uploader'), 'documented behaviour')
        self.assertRte('playlist channel', 'RTÉ', pl.get('channel'), 'documented behaviour')
        status, html = curl('https://www.rte.ie/video/category/news/')
        self.assertRte('playlist thumbnail', og(html, 'og:image'), pl.get('thumbnail'), 'category page og:image')

    def test_entries_match_feed(self):
        """Category entries match RTÉ's video feed, in order."""
        entries = self.pl['entries']
        self.assertEqual(len(entries), 20, self.res.describe())
        for n, (raw, e) in enumerate(zip(self.raw, entries), 1):
            exp = _expected(raw)
            with self.subTest(index=n, id=exp['id']):
                for key in ('id', 'title', 'timestamp', 'upload_date', 'rte_licence'):
                    self.assertRte(key, exp[key], e.get(key), f'feed byTags=epic:News item {n}')
                if exp['duration'] is None:
                    self.assertIsNone(e.get('duration'))
                else:
                    self.assertClose('duration', exp['duration'], e.get('duration'), 0.01, 'feed')
                self.assertRte('ie_key', 'RteVideo', e.get('ie_key'), 'canonical extractor')
                self.assertRegex(e.get('url') or '', WEBPAGE_RE)
                self.assertEqual(e.get('uploader'), 'RTÉ')

    def test_news_filter_accepts_entries(self):
        """A match filter on webpage_url (as used for scheduled news downloads) accepts the entries."""
        res = ytdlp('--flat-playlist', '-I', '1:3', '--match-filters', NEWS_RTE_FILTER, '--print', 'id',
                    'https://www.rte.ie/video/category/News/')
        self.assertOk(res)
        self.assertEqual(res.stdout.split(), [guid_to_id(e['guid']) for e in self.raw[:3]],
                         f'webpage_url match filter rejected entries\n{res.describe()}')

    def test_licence_filter_in_flat_entries(self):
        """The Ireland-only licence can be filtered on from the playlist alone."""
        geo = [e for e in self.pl['entries'] if e.get('rte_licence') in ('ROI', 'IOI')]
        res = ytdlp('--flat-playlist', '-I', '1:20', '--match-filter', 'rte_licence!=?ROI & rte_licence!=?IOI',
                    '--print', 'id', 'https://www.rte.ie/video/category/news/')
        self.assertOk(res)
        self.assertEqual(len(res.stdout.split()), 20 - len(geo))

    def test_break_on_existing(self):
        """--break-on-existing stops at the first archived item."""
        with tempfile.TemporaryDirectory() as tmp:
            arch = os.path.join(tmp, 'archive.txt')
            with open(arch, 'w') as f:
                f.write(f'rtevideo {guid_to_id(self.raw[3]["guid"])}\n')
            res = ytdlp('--flat-playlist', '--lazy-playlist', '--break-on-existing', '--download-archive', arch,
                        '--print', 'id', 'https://www.rte.ie/video/category/news/')
        self.assertEqual(res.returncode, 101, res.describe())
        self.assertEqual(res.stdout.split(), [guid_to_id(e['guid']) for e in self.raw[:3]])

    def test_date_filter_does_not_open_items(self):
        """Date filters work from the list alone, without opening every item."""
        dates = [_expected(e)['upload_date'] for e in self.raw[:8]]
        cutoff = sorted(set(dates))[-1] if len(set(dates)) == 1 else sorted(set(dates))[1]
        res = ytdlp('-I', '1:8', '--simulate', '--dateafter', cutoff, '--print', 'id',
                    'https://www.rte.ie/video/category/news/')
        self.assertOk(res)
        expected = [_expected(e)['id'] for e in self.raw[:8] if _expected(e)['upload_date'] >= cutoff]
        self.assertEqual(res.stdout.split(), expected, f'--dateafter {cutoff}\n{res.describe()}')
        opened = set(re.findall(r'\[rte:video\] (\S+): Downloading feed', res.stderr))
        self.assertLessEqual(opened, set(expected), f'rejected items were opened: {opened - set(expected)}')

    def test_feed_cap_1000(self):
        """RTÉ's feed stops at 1,000 items; asking past that ends cleanly instead of erroring."""
        status, _ = feed({'range': '1001-1001', 'byTags': 'epic:News'}, 'guid')
        n1000 = feed_entries({'range': '991-1000', 'byTags': 'epic:News'}, 'guid')
        res = ytdlp('--flat-playlist', '-I', '995:1005', '--print', '%(playlist_index)s',
                    'https://www.rte.ie/video/category/news/')
        self.assertOk(res, f'(RTÉ answers range 1001 with HTTP {status}) ')
        idx = [int(x) for x in res.stdout.split()]
        if len(n1000) == 10:
            epic_ok = all(e['guid'].startswith(GUID_PREFIX) for e in n1000)
            if epic_ok:
                self.assertEqual(idx, list(range(995, 1001)), 'News has >= 1000 items: listing should stop at 1000')
        self.assertTrue(all(i <= 1000 for i in idx))

    def test_nonexistent_category_no_crash(self):
        """An unknown category does not crash (it currently gives an empty playlist)."""
        res = ytdlp('--flat-playlist', '--print', 'id', 'https://www.rte.ie/video/category/nonexistentxyzzy/')
        self.assertNoTraceback(res)
        self.assertEqual(res.stdout.strip(), '')


class TestAllListing(RteTestCase):
    def test_all_listing_positions_186_200(self):
        """rte.ie/video/ lists past item 191 (it once stopped there) and -I positions match."""
        raw = []
        for start in (1, 101, 201):
            raw += feed_entries({'range': f'{start}-{start + 99}'}, 'guid,title')
        epic = [guid_to_id(e['guid']) for e in raw if e['guid'].startswith(GUID_PREFIX)]
        if len(epic) < 200:
            self.skipTest('RTÉ data changed: fewer than 200 rte.ie/video items in the first 300')
        res = ytdlp('--flat-playlist', '-I', '186:200', '--print', '%(playlist_index)s %(id)s', 'https://www.rte.ie/video/')
        self.assertOk(res)
        got = [line.split() for line in res.stdout.splitlines()]
        self.assertEqual([g[1] for g in got], epic[185:200],
                         f'RTÉ feed (rte.ie/video items only) positions 186-200 vs plugin\n{res.describe()}')
        self.assertEqual([int(g[0]) for g in got], list(range(186, 201)))

if __name__ == '__main__':
    unittest.main()
