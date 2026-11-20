"""Items from RTÉ's older lookup (rte:avgen): radio clips and episodes, Bosco and numeric dustin embeds.

Live tests pick current items from RTÉ's lists and compare with getplaylist, the stations JSON and
the radio pages (curl). Simulated tests cover data quirks that RTÉ may not have on a given day."""
import os
import re
import tempfile
import unittest

from rte_helpers import (
    RteTestCase, avgen, avgen_medium, clean, curl, embed_query, expected_media_type, info,
    live_archive_pages, live_news_articles, live_numeric_item, live_uuid_clip, live_uuid_episode,
    norm_id, og, sim_show, simulate, stations, ts_from_rte, utc_date, ytdlp,
)

UNKNOWN_ID = '99999999'
GETPLAYLIST = r'rteavgen/getplaylist/\?(?:.*&)?id={}(?:&|$)'


def _station_slug(show):
    cid = int(show['channelid']) if str(show.get('channelid') or '').isdigit() else None
    return stations().get(cid, ('radio1',))[0]


class AvgenCase(RteTestCase):
    def check_item(self, url, item_id, *, media_type=None, ext=None):
        """Full extraction of url compared with RTÉ's getplaylist for item_id. Returns the info dict."""
        status, show, _ = avgen(item_id)
        self.skipIfRte(status != 200 or not show, f'getplaylist {item_id} returned HTTP {status}')
        d, res = info(url)
        self.assertOk(res)
        src = f'getplaylist id={item_id}'
        self.assertRte('extractor_key', 'RteAvgen', d.get('extractor_key'), 'expected extractor', url)
        self.assertRte('id', str(show['itemid']).lower(), d.get('id'), src, url)
        self.assertRte('title', clean(show.get('title')), d.get('title'), src + ' (entities decoded, trimmed)', url)
        self.assertClose('duration', show['duration'] / 1000, d.get('duration'), 0.01, src, url)
        ts = ts_from_rte(show.get('published'))
        self.assertRte('timestamp', ts, d.get('timestamp'), f'{src} published={show.get("published")!r}', url)
        self.assertRte('upload_date', utc_date(ts), d.get('upload_date'), src, url)
        st = stations()
        cid = int(show['channelid']) if str(show.get('channelid') or '').isdigit() else None
        uploader = clean(show.get('channel'))
        if not uploader or uploader == 'WEB Exclusive':
            uploader = clean(show.get('provider')) or 'RTÉ'
        self.assertRte('uploader', uploader, d.get('uploader'), f'{src} channel/provider', url)
        exp_channel = st[cid][1] if cid in st else uploader
        self.assertRte('channel', exp_channel, d.get('channel'), f'live_stations/json name for channelid {cid}', url)
        exp_mt = media_type or expected_media_type(show)
        if exp_mt:
            self.assertRte('media_type', exp_mt, d.get('media_type'),
                           f'{src}: medium={avgen_medium(show)} form={show.get("form")} episode_id={show.get("episode_id")} channelid={cid}', url)
        if d.get('media_type') == 'episode':
            self.assertRte('release_timestamp', ts, d.get('release_timestamp'), 'broadcast time of an episode', url)
        else:
            self.assertIsNone(d.get('release_date'), f'clip has release_date {d.get("release_date")} ({url})')
        if avgen_medium(show) == 'audio':
            series = clean(show.get('series')) or clean(show.get('ptitle'))
            self.assertRte('series', series, d.get('series'), f'{src} series/ptitle', url)
            audio = [f for f in d.get('formats') or [] if f.get('vcodec') not in (None, 'none')]
            self.assertFalse(audio, f'audio item has formats claiming video: {[f.get("format_id") for f in audio]}')
        if ext:
            self.assertRte('ext', ext, d.get('ext'), 'documented behaviour', url)
        self.assertTrue(d.get('formats'), f'no formats for {url}')
        return d


class TestLiveItems(AvgenCase):
    @classmethod
    def setUpClass(cls):
        cls.ep = live_uuid_episode()
        cls.clip = live_uuid_clip()
        cls.numeric = live_numeric_item()

    def test_numeric_episode(self):
        """An older (numeric) episode: Irish local time, series name, description and audio-only formats."""
        self.skipIfRte(not self.numeric, 'RTÉ no longer serves any older-system (numeric) News at One episode')
        item, show = self.numeric
        item_id = norm_id(item['id'])
        url = f'https://www.rte.ie/radio/radio1/news-at-one/episodes/{item_id}/'
        d = self.check_item(url, item_id, media_type='episode')
        self.assertTrue(d.get('description'), 'description missing')

    def test_uuid_episode_routes(self):
        """A current episode gives the same metadata via every URL that reaches it, including the
        /clips/ URL RTÉ's own share links use for full episodes."""
        self.skipIfRte(not self.ep, 'no UUID episode on News at One page 1')
        routes = [
            f'https://www.rte.ie/radio/radio1/news-at-one/episodes/{self.ep}/',
            f'https://www.rte.ie/radio/radio1/clips/{self.ep}/',
            f'https://www.rte.ie/bosco/components/player/iframe.html?clipid={self.ep.upper()}',
        ]
        for url in routes:
            with self.subTest(url=url):
                d = self.check_item(url, self.ep, media_type='episode')
                self.assertTrue(d.get('release_date'))
                status, html = curl(f'https://www.rte.ie/radio/radio1/clips/{self.ep}/')
                desc = og(html, 'og:description') or og(html, 'description')
                self.assertTrue(d.get('description'), 'description missing')
                if desc:
                    self.assertRte('description', desc, d.get('description'), 'radio page og:description', url)

    def test_uuid_clip(self):
        """A current clip (part of an episode) is a clip, with no release date."""
        self.skipIfRte(not self.clip, 'no clips for Morning Ireland')
        self.check_item(f'https://www.rte.ie/radio/radio1/clips/{self.clip}/', self.clip, media_type='clip')

    def test_station_from_channelid_not_url_slug(self):
        """RTÉ serves an item under any station's URL; the station must come from RTÉ's data."""
        self.skipIfRte(not self.clip, 'no clips for Morning Ireland')
        d = self.check_item(f'https://www.rte.ie/radio/lyricfm/clips/{self.clip}/', self.clip)
        self.assertRte('channel', 'RTÉ Radio 1', d.get('channel'), 'Morning Ireland is on Radio 1 (channelid 9)')

    def test_news_video_embed(self):
        """A video embedded in a current news article (older-system player) matches RTÉ's data."""
        for url, _, embeds in live_news_articles(want=15, scan=45):
            for emb in embeds:
                q = embed_query(emb)
                item_id = q.get('clipid') or (q.get('id') if q.get('id', '').isdigit() and len(q['id']) >= 4 else None)
                if item_id:
                    self.check_item(emb, norm_id(item_id))
                    return
        self.skipIfRte(True, 'no current news article with an older-system player embed in the news feed')


class TestSimulatedItems(RteTestCase):
    def sim_info(self, url, item_id, **fields):
        d, res = info(url, faults=[simulate(GETPLAYLIST.format(re.escape(item_id)), sim_show(item_id, **fields))])
        self.assertFaultHit(res)
        self.assertOk(res)
        return d

    # SIMULATED RTÉ DATA: RTÉ's response is replaced with hand-written data in RTÉ's format as seen
    # in November 2026. If RTÉ changes that format, this test can still pass while the plugin fails
    # on real data; update the simulated data to match RTÉ's new format.
    def test_older_items_long_is_episode_short_is_clip(self):
        """Older (numeric) items say 'long' for episodes and 'short' for clips."""
        d = self.sim_info('https://www.rte.ie/radio/radio1/clips/99999901/', '99999901', form='long')
        self.assertEqual((d['media_type'], d['release_date']), ('episode', '20260115'))
        d = self.sim_info('https://www.rte.ie/radio/radio1/clips/99999902/', '99999902', form='short')
        self.assertEqual((d['media_type'], d.get('release_date')), ('clip', None))

    # SIMULATED RTÉ DATA: RTÉ's response is replaced with hand-written data in RTÉ's format as seen
    # in November 2026. If RTÉ changes that format, this test can still pass while the plugin fails
    # on real data; update the simulated data to match RTÉ's new format.
    def test_newer_items_part_of_an_episode_is_clip(self):
        """Newer (UUID) items name their parent episode when they are a clip of it."""
        ep = 'aaaaaaaa-0000-4000-8000-000000000001'
        clip = 'aaaaaaaa-0000-4000-8000-000000000002'
        d = self.sim_info(f'https://www.rte.ie/radio/radio1/clips/{ep}/', ep)
        self.assertEqual(d['media_type'], 'episode', 'no episode_id: the item is an episode itself')
        d = self.sim_info(f'https://www.rte.ie/radio/radio1/clips/{clip}/', clip, episode_id=ep)
        self.assertEqual((d['media_type'], d.get('release_date')), ('clip', None))

    # SIMULATED RTÉ DATA: RTÉ's response is replaced with hand-written data in RTÉ's format as seen
    # in November 2026. If RTÉ changes that format, this test can still pass while the plugin fails
    # on real data; update the simulated data to match RTÉ's new format.
    def test_archive_audio_has_no_release_date(self):
        """Archive items are not on a radio station (no station number, or 13), and their published
        date is an upload date, not a broadcast date."""
        for item_id, channelid, channel in (('99999903', None, None), ('99999907', '13', 'WEB Exclusive')):
            with self.subTest(channelid=channelid):
                d = self.sim_info(f'https://www.rte.ie/media-embed/dustin/?id={item_id}', item_id,
                                  channelid=channelid, channel=channel, provider='RTÉ', form='long', series=None,
                                  ptitle='RTÉ Archives Audio', published='2014-02-18T11:32:03')
                self.assertEqual(d['media_type'], 'clip')
                self.assertIsNone(d.get('release_date'))
                self.assertIsNone(d.get('release_timestamp'))
                self.assertEqual((d['series'], d['uploader'], d['channel']), ('RTÉ Archives Audio', 'RTÉ', 'RTÉ'))

    # SIMULATED RTÉ DATA: RTÉ's response is replaced with hand-written data in RTÉ's format as seen
    # in November 2026. If RTÉ changes that format, this test can still pass while the plugin fails
    # on real data; update the simulated data to match RTÉ's new format.
    def test_web_exclusive_is_not_the_uploader(self):
        """RTÉ labels some news videos 'WEB Exclusive' where the channel goes; that is not an uploader."""
        d = self.sim_info('https://www.rte.ie/bosco/components/player/iframe.html?clipid=99999904', '99999904',
                          channel='WEB Exclusive', channelid='13', medium='video', provider=None)
        self.assertEqual((d['uploader'], d['media_type']), ('RTÉ', 'clip'))

    # SIMULATED RTÉ DATA: RTÉ's response is replaced with hand-written data in RTÉ's format as seen
    # in November 2026. If RTÉ changes that format, this test can still pass while the plugin fails
    # on real data; update the simulated data to match RTÉ's new format.
    def test_titles_are_cleaned(self):
        """Some RTÉ titles have stray spaces, NUL characters or HTML entities; titles come out clean."""
        d = self.sim_info('https://www.rte.ie/radio/radio1/clips/99999905/', '99999905',
                          title='  News at One\x00 &amp; more  ')
        self.assertEqual(d['title'], 'News at One & more')

    # SIMULATED RTÉ DATA: RTÉ's response is replaced with hand-written data in RTÉ's format as seen
    # in November 2026. If RTÉ changes that format, this test can still pass while the plugin fails
    # on real data; update the simulated data to match RTÉ's new format.
    def test_station_from_channelid(self):
        """The station comes from RTÉ's station number, whatever station the URL names."""
        d = self.sim_info('https://www.rte.ie/radio/radio1/clips/99999906/', '99999906',
                          channelid='16', channel='RTÉ lyric fm')
        self.assertEqual((d['channel'], d['uploader']), ('RTÉ Lyric FM', 'RTÉ lyric fm'))


class TestErrorsAndInference(RteTestCase):
    def test_unknown_item_gives_rtes_message(self):
        """An unknown item passes on RTÉ's own explanation instead of a bare HTTP 404."""
        status, show, raw = avgen(UNKNOWN_ID)
        self.assertIsNone(show, f'item {UNKNOWN_ID} exists now: pick a higher unused ID')
        message = clean((raw or {}).get('message'))
        res = ytdlp('--simulate', f'https://www.rte.ie/radio/radio1/clips/{UNKNOWN_ID}/')
        self.assertErrorContains(res, 'RTÉ said:' if message else 'Item not found')
        if message:
            self.assertIn(message, res.stderr, f'RTÉ (HTTP {status}) says {message!r}')
        self.assertNotIn('HTTP Error 404', res.stderr)

    def test_station_roots_and_live_player_not_items(self):
        """Station home pages and live-radio player embeds are not mistaken for items."""
        for url in ['https://www.rte.ie/radio/radio1/', 'https://www.rte.ie/radio/2fm', 'https://www.rte.ie/radio/lyricfm/',
                    'https://www.rte.ie/media-embed/dustin/?id=9&radioUI=true&pl_pillar=Radio&clipid=9',
                    'https://www.rte.ie/bosco/components/player/iframe.html?clipid=123']:
            with self.subTest(url=url):
                res = ytdlp('--simulate', url)
                self.assertNotRegex(res.stderr, r'\[rte:avgen\]', f'live player / station id claimed as an item\n{res.describe()}')
                self.assertNoTraceback(res)

    def test_id_anchored(self):
        """Junk after an item ID in the URL is not silently ignored."""
        res = ytdlp('--simulate', 'https://www.rte.ie/radio/radio1/clips/10507902abc/')
        self.assertNotIn('[rte:avgen] 10507902', res.stderr, 'trailing junk after the ID was accepted')

    def test_get_lookup_failure_gives_clean_error(self):
        """If RTÉ's lookup service is down, the result is a clean error, not a crash."""
        res = ytdlp('--simulate', 'https://www.rte.ie/radio/radio1/clips/10507902/',
                    faults=[{'match': r'rteavgen/getplaylist', 'status': 503, 'times': -1}])
        self.assertFaultHit(res)
        self.assertNotEqual(res.returncode, 0)
        self.assertNotIn('Traceback', res.output)


class TestArchiveKeys(RteTestCase):
    """Same item -> one key `rteavgen <id>`; different items never share a key. Uses yt-dlp's real
    download archive (--force-write-archive with --simulate writes keys without downloading)."""

    def _write_key(self, arch, url):
        res = ytdlp('--simulate', '--force-write-archive', '--download-archive', arch, url)
        self.assertOk(res, f'{url}: ')
        return res

    def _already(self, arch, url):
        res = ytdlp('--simulate', '--download-archive', arch, url)
        self.assertNoTraceback(res)
        return 'has already been recorded in the archive' in res.output, res

    def _check_routes(self, key, routes):
        with tempfile.TemporaryDirectory() as tmp:
            arch = os.path.join(tmp, 'archive.txt')
            self._write_key(arch, routes[0])
            with open(arch) as f:
                keys = f.read().split('\n')
            self.assertEqual([k for k in keys if k], [key], f'archive key from {routes[0]}')
            for url in routes[1:]:
                with self.subTest(route=url):
                    seen, res = self._already(arch, url)
                    self.assertTrue(seen, f'{url} not recognised as {key}\n{res.describe()}')

    def test_numeric_radio_routes(self):
        """One older item reached through station, episode, other-station and zero-padded URLs gets one archive key."""
        found = live_numeric_item()
        self.skipIfRte(not found, 'RTÉ no longer serves any older-system (numeric) News at One episode')
        item, show = found
        item_id = norm_id(item['id'])
        slug = _station_slug(show)
        self._check_routes(f'rteavgen {item_id}', [
            f'https://www.rte.ie/radio/{slug}/clips/{item_id}/',
            f'https://www.rte.ie/radio/radio1/news-at-one/episodes/{item_id}/',
            f'https://www.rte.ie/radio/lyricfm/clips/{item_id}/',
            f'https://www.rte.ie/radio/{slug}/clips/0{item_id}/',
        ])

    def test_archive_page_and_numeric_dustin(self):
        """An archive page and the embed inside it get one archive key."""
        for page_url, _, embeds in live_archive_pages():
            ids = [embed_query(e).get('id', '') for e in embeds if 'dustin' in e]
            item_id = next((norm_id(i) for i in ids if i.isdigit() and len(i) >= 4), None)
            if item_id and len(embeds) == 1:
                self._check_routes(f'rteavgen {item_id}', [
                    page_url,
                    f'https://www.rte.ie/media-embed/dustin/?id={item_id}',
                    f'https://www.rte.ie/media-embed/dustin/?id=0{item_id}',
                    f'https://www.rte.ie/bosco/components/player/iframe.html?clipid={item_id}',
                ])
                return
        self.skipIfRte(True, 'no archive page with a single older-system embed on the RTÉ Archives home page')

    def test_uuid_episode_routes(self):
        """A current episode gets one archive key via every URL that reaches it."""
        ep = live_uuid_episode()
        self.skipIfRte(not ep, 'no UUID episode on News at One page 1')
        self._check_routes(f'rteavgen {ep}', [
            f'https://www.rte.ie/radio/radio1/news-at-one/episodes/{ep}/',
            f'https://www.rte.ie/radio/radio1/clips/{ep}/',
            f'https://www.rte.ie/radio/radio1/clips/{ep.upper()}/',
            f'https://www.rte.ie/bosco/components/player/iframe.html?clipid={ep}',
        ])

    def test_different_items_different_keys(self):
        """Different items never share an archive key (so none is wrongly skipped)."""
        urls = []
        ep, clip, numeric = live_uuid_episode(), live_uuid_clip(), live_numeric_item()
        if ep:
            urls.append(f'https://www.rte.ie/radio/radio1/news-at-one/episodes/{ep}/')
        if clip:
            urls.append(f'https://www.rte.ie/radio/radio1/clips/{clip}/')
        if numeric:
            urls.append(f'https://www.rte.ie/radio/radio1/news-at-one/episodes/{norm_id(numeric[0]["id"])}/')
        for _, _, embeds in live_archive_pages(want=2):
            urls += [e for e in embeds if 'dustin' in e and embed_query(e).get('id', '').isdigit()][:1]
        self.skipIfRte(len(urls) < 3, f'fewer than 3 current items found ({urls})')
        with tempfile.TemporaryDirectory() as tmp:
            arch = os.path.join(tmp, 'archive.txt')
            for url in urls:
                seen, res = self._already(arch, url)
                self.assertFalse(seen, f'{url} wrongly matched an earlier item\'s key\n{res.describe()}')
                self._write_key(arch, url)
            with open(arch) as f:
                keys = [k for k in f.read().split('\n') if k]
        self.assertEqual(len(keys), len(set(keys)), keys)
        self.assertTrue(all(re.fullmatch(r'rteavgen [0-9a-f-]+', k) for k in keys), keys)


if __name__ == '__main__':
    unittest.main()
