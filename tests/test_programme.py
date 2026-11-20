"""Radio programme playlists (rte:radio:programme).

Expected values come from RTÉ's episodes/json and clips/json (curl). Some tests inject faults
(HTTP 500, 404, timeouts) into yt-dlp's requests for one URL, and some replace one list with
simulated data; everything else is real RTÉ traffic."""
import os
import re
import tempfile
import unittest

from rte_helpers import (
    RteTestCase, clean, curl, flat, info, norm_id, og, programme_page, sim_list_item, simulate, stations,
    ts_from_rte, utc_date, ytdlp,
)

NAO = 'https://www.rte.ie/radio/radio1/news-at-one/'
PAGE2 = r'/radio/radio1/news-at-one/episodes/json/\?page=2$'
PAGE1 = r'/radio/radio1/news-at-one/episodes/json/\?page=1$'


def _ts(item):
    ts = ts_from_rte(item.get('published'))
    if ts is None:
        b = item.get('BroadcastDateUnixTime')
        ts = int(b) if b and int(b) > 0 else None
    return ts


def _expected_entry(item, station, programme, kind):
    st = stations()
    item_id = norm_id(item.get('id') or item['itemid'])
    ts = _ts(item)
    cid = int(item['channelid']) if str(item.get('channelid') or '').isdigit() else None
    url = (f'https://www.rte.ie/radio/{station}/{programme}/episodes/{item_id}/' if kind == 'episodes'
           else f'https://www.rte.ie/radio/{station}/clips/{item_id}/')
    return {
        'id': item_id, 'url': url, 'title': clean(item.get('title')), 'timestamp': ts, 'upload_date': utc_date(ts),
        'release_date': utc_date(ts) if kind == 'episodes' else None,
        'duration': None if item_id.isdigit() else (item['duration'] / 1000 if item.get('duration') else None),
        'series': clean(item.get('programme_title')),
        'channel': st[cid][1] if cid in st else None,
    }



class ProgrammeCase(RteTestCase):
    def check_entries(self, entries, raw, station, programme, kind, start=1):
        self.assertEqual(len(entries), len(raw), f'RTÉ list has {len(raw)} items here; plugin gave {len(entries)}')
        for n, (item, e) in enumerate(zip(raw, entries), start):
            exp = _expected_entry(item, station, programme, kind)
            with self.subTest(index=n, id=exp['id']):
                src = f'{kind}/json item {n}'
                for key in ('id', 'url', 'title', 'timestamp', 'upload_date', 'series', 'channel'):
                    self.assertRte(key, exp[key], e.get(key), src)
                if kind == 'episodes':
                    self.assertRte('release_date', exp['release_date'], e.get('release_date'), src)
                if exp['duration'] is None:
                    self.assertIsNone(e.get('duration'), f'{src}: numeric items must not carry the list duration (the list value is wrong for them)')
                else:
                    self.assertClose('duration', exp['duration'], e.get('duration'), 0.01, src)
                self.assertRte('ie_key', 'RteAvgen', e.get('ie_key'), 'canonical extractor')
                self.assertNotIn('\x00', e.get('title') or '')


class TestNewsAtOne(ProgrammeCase):
    @classmethod
    def setUpClass(cls):
        s1, cls.p1 = programme_page('radio1', 'news-at-one', 'episodes', 1)
        s2, cls.p2 = programme_page('radio1', 'news-at-one', 'episodes', 2)
        if not cls.p1 or len(cls.p1) < 100 or not cls.p2:
            raise unittest.SkipTest(f'RTÉ data changed: News at One pages 1/2 returned HTTP {s1}/{s2}')

    def test_first_entries_and_playlist_fields(self):
        """Playlist entries and playlist fields match RTÉ's episode list."""
        pl, res = flat(NAO, '-I', '1:10')
        self.assertOk(res)
        self.assertRte('playlist id', 'news-at-one', pl.get('id'), 'URL')
        _, html = curl(NAO)
        self.assertRte('playlist title', og(html, 'twitter:title'), pl.get('title'), 'programme page twitter:title')
        self.assertRte('playlist uploader', 'RTÉ Radio 1', pl.get('uploader'), 'stations JSON')
        self.assertRte('playlist channel', 'RTÉ Radio 1', pl.get('channel'), 'stations JSON')
        self.check_entries(pl['entries'], self.p1[:10], 'radio1', 'news-at-one', 'episodes')
        for e in pl['entries']:
            self.assertEqual(e.get('media_type'), 'episode')

    def test_page_boundary_and_no_gap_filling_by_default(self):
        """Crossing the 100-item page boundary works, and the experimental gap search stays off by default."""
        with tempfile.TemporaryDirectory() as tmp:
            pl, res = flat(NAO, '-I', '99:102', '--print-traffic', '--download-archive', os.path.join(tmp, 'a.txt'))
        self.assertOk(res)
        self.check_entries(pl['entries'], self.p1[98:100] + self.p2[:2], 'radio1', 'news-at-one', 'episodes', 99)
        self.assertIn('episodes/json/?page=2', res.output, '--print-traffic shows no requests: the check below would be vacuous')
        self.assertNotIn('rteavgen', res.output,'rteavgen lookups made during a plain listing (gap filling on?)')
        self.assertNotRegex(res.output, r'skips .* searching|missing episodes', 'gap search message printed')

    def test_no_duplicates_first_200(self):
        """No item appears twice across a page boundary."""
        pl, res = flat(NAO, '-I', '1:200')
        self.assertOk(res)
        ids = [e['id'] for e in pl['entries']]
        self.assertEqual(len(ids), len(set(ids)), 'duplicate IDs')
        raw = [norm_id(i['id']) for i in self.p1 + self.p2][:200]
        self.assertEqual(ids, list(dict.fromkeys(raw)), 'order/ids differ from RTÉ pages 1-2')

    def test_url_variants(self):
        """Programme URL variants (case, slash, http, no www, query string) give the same playlist."""
        first = norm_id(self.p1[0]['id'])
        for url in ['https://www.rte.ie/radio/radio1/news-at-one', 'https://www.rte.ie/radio/radio1/news-at-one/episodes/',
                    'https://www.rte.ie/radio/radio1/news-at-one/episodes', 'http://rte.ie/radio/radio1/news-at-one/',
                    'https://www.rte.ie/radio/radio1/news-at-one/?utm_source=x', 'https://www.rte.ie/radio/radio1/news-at-one/#top',
                    'https://www.rte.ie/radio/Radio1/News-At-One/']:
            with self.subTest(url=url):
                pl, res = flat(url, '-I', '1:1')
                self.assertOk(res)
                self.assertEqual(pl.get('extractor_key'), 'RteRadioProgramme')
                self.assertEqual(pl.get('id'), 'news-at-one')
                self.assertEqual(pl.get('uploader'), 'RTÉ Radio 1', 'uploader lost (uppercase URL)')
                self.assertEqual([e['id'] for e in pl['entries']], [first])
                self.assertEqual(pl['entries'][0].get('channel'), 'RTÉ Radio 1')

    def test_flat_matches_full(self):
        """Fields in a flat playlist entry match a full extraction of the same item."""
        picks = [next((i for i in self.p1 if not str(i['id']).isdigit()), None),
                 next((i for i in self.p1 if str(i['id']).isdigit()), None)]
        for item in filter(None, picks):
            exp = _expected_entry(item, 'radio1', 'news-at-one', 'episodes')
            with self.subTest(id=exp['id']):
                pl, res = flat(NAO, '-I', str(self.p1.index(item) + 1))
                e = pl['entries'][0]
                d, res2 = info(e['url'])
                self.assertOk(res2)
                for key in ('id', 'timestamp', 'upload_date', 'release_date', 'channel', 'media_type', 'series'):
                    self.assertEqual(e.get(key), d.get(key), f'{key}: flat entry {e.get(key)!r} vs full extraction {d.get(key)!r}')

    def test_break_on_existing(self):
        """--break-on-existing stops at the first archived item."""
        with tempfile.TemporaryDirectory() as tmp:
            arch = os.path.join(tmp, 'archive.txt')
            with open(arch, 'w') as f:
                f.write(f'rteavgen {norm_id(self.p1[2]["id"])}\n')
            res = ytdlp('--flat-playlist', '--lazy-playlist', '--break-on-existing', '--download-archive', arch,
                        '--print', 'id', NAO)
        self.assertEqual(res.returncode, 101, res.describe())
        self.assertEqual(res.stdout.split(), [norm_id(i['id']) for i in self.p1[:2]])

    def test_date_filter_does_not_open_items(self):
        """Date filters work from the list alone, without opening every item."""
        dates = [utc_date(_ts(i)) for i in self.p1[:8]]
        cutoff = sorted(set(dates))[len(set(dates)) // 2]
        res = ytdlp('-I', '1:8', '--simulate', '--dateafter', cutoff, '--print', 'id', NAO)
        self.assertOk(res)
        expected = [norm_id(i['id']) for i, dte in zip(self.p1[:8], dates) if dte >= cutoff]
        self.assertEqual(res.stdout.split(), expected, f'--dateafter {cutoff} (RTÉ dates {dates})')
        opened = set(re.findall(r'\[rte:avgen\] (\S+): ', res.stderr))
        self.assertLessEqual(opened, set(expected), f'rejected items were opened: {opened - set(expected)}')

    # ---- fault injection (one URL's response replaced; the rest is real RTÉ traffic)

    def test_mid_list_500_is_not_silent(self):
        """A lasting server error partway through a list fails loudly instead of silently cutting it short."""
        res = ytdlp('--flat-playlist', '-I', '99:102', '--print', 'id', NAO,
                    faults=[{'match': PAGE2, 'status': 500, 'times': -1}])
        self.assertFaultHit(res)
        self.assertNotEqual(res.returncode, 0, f'HTTP 500 on page 2 silently ended the list\n{res.describe()}')
        self.assertIn('500', res.stderr)
        self.assertNoTraceback(res)

    def test_mid_list_500_retried(self):
        """A single server error partway through a list is retried."""
        res = ytdlp('--flat-playlist', '-I', '99:102', '--print', 'id', NAO,
                    faults=[{'match': PAGE2, 'status': 500, 'times': 1}])
        self.assertFaultHit(res)
        self.assertOk(res, 'one transient 500 should be retried: ')
        self.assertEqual(res.stdout.split(), [norm_id(i['id']) for i in self.p1[98:100] + self.p2[:2]])

    def test_timeout_retried(self):
        """A single timeout partway through a list is retried."""
        res = ytdlp('--flat-playlist', '-I', '99:102', '--print', 'id', NAO,
                    faults=[{'match': PAGE2, 'status': 'timeout', 'times': 1}])
        self.assertFaultHit(res)
        self.assertOk(res, 'one timeout should be retried: ')
        self.assertEqual(len(res.stdout.split()), 4)

    def test_404_after_full_page_ends_cleanly(self):
        """RTÉ answers 404 after a list's exactly full last page; that ends the list, not the run."""
        res = ytdlp('--flat-playlist', '-I', '99:102', '--print', 'id', NAO,
                    faults=[{'match': PAGE2, 'status': 404, 'times': -1}])
        self.assertFaultHit(res)
        self.assertOk(res, '404 after a full page must end the list with exit 0: ')
        self.assertEqual(res.stdout.split(), [norm_id(i['id']) for i in self.p1[98:100]])

    def test_500_on_page_1_is_clean_error(self):
        """A server error on the first page gives a clear \"no episode list\" error."""
        res = ytdlp('--flat-playlist', '-I', '1:2', NAO, faults=[{'match': PAGE1, 'status': 500, 'times': -1}])
        self.assertFaultHit(res)
        self.assertErrorContains(res, 'RTÉ has no episode list for this programme')


class TestOtherProgrammes(ProgrammeCase):
    def test_clips_playlist(self):
        """<programme>/clips/ gives the programme's clips as their own playlist, newest first."""
        _, raw = programme_page('radio1', 'morning-ireland', 'clips')
        self.skipIfRte(not raw, 'no Morning Ireland clips')
        pl, res = flat('https://www.rte.ie/radio/radio1/morning-ireland/clips/', '-I', '1:5')
        self.assertOk(res)
        self.assertRte('playlist id', 'morning-ireland-clips', pl.get('id'), 'documented: <programme>-clips')
        self.assertTrue((pl.get('title') or '').endswith(' (clips)'), pl.get('title'))
        self.check_entries(pl['entries'], raw[:5], 'radio1', 'morning-ireland', 'clips')
        for e in pl['entries']:
            self.assertEqual(e.get('media_type'), 'clip')
            self.assertIsNone(e.get('release_date'), 'clips have no release date')


SIM_PROGRAMME = 'https://www.rte.ie/radio/radio1/simulated-programme/'


def _sim_list(items):
    """An episodes/json or clips/json page in RTÉ's shape (its page counts are unreliable anyway)."""
    return {'page': 1, 'num_pages': 1, 'num_per_page': 100, 'total_items': len(items), 'programmes': items}


class TestSimulatedLists(ProgrammeCase):
    # SIMULATED RTÉ DATA: RTÉ's response is replaced with hand-written data in RTÉ's format as seen
    # in November 2026. If RTÉ changes that format, this test can still pass while the plugin fails
    # on real data; update the simulated data to match RTÉ's new format.
    def test_empty_lists_error(self):
        """A programme whose list RTÉ leaves empty gives a clear error (no guessing from other sources)."""
        for kind in ('episodes', 'clips'):
            with self.subTest(kind=kind):
                url = SIM_PROGRAMME + ('clips/' if kind == 'clips' else '')
                res = ytdlp('--flat-playlist', url, faults=[
                    simulate(rf'/radio/radio1/simulated-programme/{kind}/json/', _sim_list([]))])
                self.assertFaultHit(res)
                self.assertErrorContains(res, f'RTÉ has no {kind[:-1]} list for this programme')

    # SIMULATED RTÉ DATA: RTÉ's response is replaced with hand-written data in RTÉ's format as seen
    # in November 2026. If RTÉ changes that format, this test can still pass while the plugin fails
    # on real data; update the simulated data to match RTÉ's new format.
    def test_short_list_complete(self):
        """A list shorter than one page is listed completely, and no further page is asked for."""
        items = [sim_list_item(f'bbbbbbbb-0000-4000-8000-00000000000{n}', f'Simulated episode {n}',
                               f'2026-11-0{n}T13:00:00Z') for n in (3, 2, 1)]
        pl, res = flat(SIM_PROGRAMME, '--print-traffic', faults=[
            simulate(r'/radio/radio1/simulated-programme/episodes/json/\?page=1$', _sim_list(items))])
        self.assertFaultHit(res)
        self.assertOk(res)
        self.check_entries(pl['entries'], items, 'radio1', 'simulated-programme', 'episodes')
        self.assertNotIn('episodes/json/?page=2', res.output, 'asked for a page after a short last page')

    # SIMULATED RTÉ DATA: RTÉ's response is replaced with hand-written data in RTÉ's format as seen
    # in November 2026. If RTÉ changes that format, this test can still pass while the plugin fails
    # on real data; update the simulated data to match RTÉ's new format.
    def test_placeholder_dates_are_no_dates(self):
        """RTÉ marks unknown dates with 0001-01-01 and negative Unix times; those give no date at all."""
        items = [sim_list_item('cccccccc-0000-4000-8000-000000000001', 'Simulated clip', '0001-01-01T00:00:00',
                               BroadcastDateUnixTime=-62135596800)]
        pl, res = flat(SIM_PROGRAMME + 'clips/', faults=[
            simulate(r'/radio/radio1/simulated-programme/clips/json/\?page=1$', _sim_list(items))])
        self.assertFaultHit(res)
        self.assertOk(res)
        e = pl['entries'][0]
        self.assertIsNone(e.get('timestamp'))
        self.assertIsNone(e.get('upload_date'))

    def test_non_programme_paths_error(self):
        """Radio pages that are not programmes give an error, not an empty playlist."""
        for url in ['https://www.rte.ie/radio/radio1/schedule/', 'https://www.rte.ie/radio/radio1/no-such-programme-xyzzy/',
                    'https://www.rte.ie/radio/radio1/clips/', 'https://www.rte.ie/radio/podcasts/']:
            with self.subTest(url=url):
                res = ytdlp('--flat-playlist', '--print', 'id', url)
                self.assertNotEqual(res.returncode, 0, f'{url} gave a playlist instead of an error\n{res.describe()}')
                self.assertNoTraceback(res)
                self.assertEqual(res.stdout.strip(), '')

if __name__ == '__main__':
    unittest.main()
