"""Items from RTÉ's older lookup (rte:avgen): radio clips and episodes, Bosco and numeric dustin embeds.

Expected values come from rteavgen getplaylist, the stations JSON and the radio pages (curl)."""
import os
import re
import tempfile
import unittest

from rte_helpers import (
    RteTestCase, avgen, avgen_medium, clean, curl, expected_media_type, info, og, programme_page, stations,
    ts_from_rte, utc_date, ytdlp,
)

UNKNOWN_ID = '99999999'


class AvgenCase(RteTestCase):
    def check_item(self, url, item_id, *, media_type=None, release_date=None, ext=None):
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
        if release_date:
            self.assertRte('release_date', release_date, d.get('release_date'), 'pinned', url)
        if avgen_medium(show) == 'audio':
            series = clean(show.get('series')) or clean(show.get('ptitle'))
            self.assertRte('series', series, d.get('series'), f'{src} series/ptitle', url)
        if ext:
            self.assertRte('ext', ext, d.get('ext'), 'documented behaviour', url)
        self.assertTrue(d.get('formats'), f'no formats for {url}')
        return d


class TestPinnedItems(AvgenCase):
    def test_news_at_one_numeric_episode(self):
        # 1 April 2026, 13:00 Irish summer time = 12:00 UTC (gap-filling case; always reachable directly)
        """An older (numeric) episode: Irish summer time, series name and description are right."""
        d = self.check_item('https://www.rte.ie/radio/radio1/news-at-one/episodes/11787175/', '11787175',
                            media_type='episode', release_date='20260401', ext='m4a')
        self.assertRte('timestamp', 1775044800, d.get('timestamp'), 'pinned: 2026-04-01T13:00 IST')
        self.assertTrue(d.get('description'), 'description missing')
        self.assertRte('series', 'News At One', d.get('series'), 'getplaylist ptitle')
        fmts = [f for f in d['formats'] if f.get('ext') == 'm4a' or f.get('protocol', '').startswith('m3u8')]
        self.assertTrue(all(f.get('vcodec') == 'none' for f in fmts), 'audio-only HLS formats claim video')

    def test_episode_under_clips_url_is_episode(self):
        # RTÉ's own share link for a full Gold episode uses /clips/: media_type must still be episode
        """RTÉ's own share links put full episodes under /clips/; they must still count as episodes."""
        d = self.check_item('https://www.rte.ie/radio/gold/clips/34788141-b67a-4ee4-b7a0-b4b4014cb486/',
                            '34788141-b67a-4ee4-b7a0-b4b4014cb486', media_type='episode',
                            release_date='20260828', ext='mp3')
        self.assertRte('channel', 'RTÉ Gold', d.get('channel'), 'stations JSON')

    def test_real_clip_is_clip(self):
        """A real clip (part of an episode) is a clip, with no release date."""
        self.check_item('https://www.rte.ie/radio/rnag/clips/5f025eb2-d6d0-449a-be23-b50000d5132f/',
                        '5f025eb2-d6d0-449a-be23-b50000d5132f', media_type='clip', ext='mp3')

    def test_station_from_channelid_not_url_slug(self):
        # 10507902 is a lyric fm item; rte.ie serves it under any station's URL
        """RTÉ serves an item under any station's URL; the station must come from RTÉ's data."""
        d = self.check_item('https://www.rte.ie/radio/radio1/clips/10507902/', '10507902')
        self.assertRte('channel', 'RTÉ Lyric FM', d.get('channel'), 'channelid 16 in live_stations/json')
        self.assertEqual(d.get('uploader'), 'RTÉ lyric fm', 'uploader is RTÉ\'s own value (getplaylist channel)')
        d2, res = info('https://www.rte.ie/radio/lyricfm/clips/10507902/')
        self.assertOk(res)
        self.assertEqual((d2['channel'], d2['id']), (d['channel'], d['id']))

    def test_archive_audio_dustin_has_no_release_date(self):
        """Archive audio has no station, and its published date is an upload date, not a broadcast date."""
        d = self.check_item('https://www.rte.ie/media-embed/dustin/?id=3632796', '3632796', media_type='clip', ext='m4a')
        self.assertIsNone(d.get('release_date'))
        self.assertIsNone(d.get('release_timestamp'))

    def test_bosco_news_video(self):
        """A news video embed: RTÉ's \"WEB Exclusive\" label is not used as the uploader."""
        d = self.check_item('https://www.rte.ie/bosco/components/player/iframe.html?clipid=22640012&autostart=false&app=true',
                            '22640012', media_type='clip', ext='mp4')
        self.assertEqual(d.get('uploader'), 'RTÉ', 'WEB Exclusive must be ignored')

    def test_trailing_space_title_trimmed(self):
        """Some RTÉ titles end in a space or contain double spaces; titles come out clean."""
        status, show, _ = avgen('10603799')
        self.skipIfRte(not show, 'item 10603799 gone')
        d = self.check_item('https://www.rte.ie/media-embed/dustin/?id=10603799', '10603799', media_type='clip')
        self.assertEqual(d['title'], d['title'].strip())
        self.assertNotRegex(d['title'], r'\s{2}')


class TestDynamicItems(AvgenCase):
    @classmethod
    def setUpClass(cls):
        _, eps = programme_page('radio1', 'news-at-one')
        cls.ep = next((str(i['id']) for i in eps or [] if not str(i['id']).isdigit()), None)
        _, clips = programme_page('radio1', 'morning-ireland', 'clips')
        cls.clip = str(clips[0]['id']) if clips else None

    def test_uuid_episode_routes(self):
        """A current episode gives the same metadata (or archive key) via every URL that reaches it."""
        if not self.ep:
            self.skipTest('RTÉ data changed: no UUID episode on News at One page 1')
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
        """A current clip: metadata matches RTÉ's data."""
        if not self.clip:
            self.skipTest('RTÉ data changed: no clips for Morning Ireland')
        self.check_item(f'https://www.rte.ie/radio/radio1/clips/{self.clip}/', self.clip, ext='mp3')


class TestErrorsAndInference(RteTestCase):
    def test_unknown_item_gives_rtes_message(self):
        """An unknown item passes on RTÉ's own explanation instead of a bare HTTP 404."""
        status, show, raw = avgen(UNKNOWN_ID)
        self.skipIfRte(show is not None, f'item {UNKNOWN_ID} exists now')
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
        """One older item reached through station, episode and zero-padded URLs gets one archive key."""
        self._check_routes('rteavgen 10507902', [
            'https://www.rte.ie/radio/lyricfm/clips/10507902/',
            'https://www.rte.ie/radio/radio1/clips/10507902/',
            'https://www.rte.ie/radio/lyricfm/gloria/episodes/10507902/',
            'https://www.rte.ie/radio/lyricfm/clips/010507902/',
        ])

    def test_archive_page_and_numeric_dustin(self):
        """An archive page and the embed inside it get one archive key."""
        self._check_routes('rteavgen 10603799', [
            'https://www.rte.ie/archives/2016/0806/806509-aer-lingus-launch-glamorous-new-uniform/',
            'https://www.rte.ie/media-embed/dustin/?id=10603799',
            'https://www.rte.ie/media-embed/dustin/?id=010603799',
            'https://www.rte.ie/bosco/components/player/iframe.html?clipid=10603799',
        ])

    def test_uuid_episode_routes(self):
        """A current episode gives the same metadata (or archive key) via every URL that reaches it."""
        _, eps = programme_page('radio1', 'news-at-one')
        ep = next((str(i['id']) for i in eps or [] if not str(i['id']).isdigit()), None)
        if not ep:
            self.skipTest('RTÉ data changed: no UUID episode')
        self._check_routes(f'rteavgen {ep}', [
            f'https://www.rte.ie/radio/radio1/news-at-one/episodes/{ep}/',
            f'https://www.rte.ie/radio/radio1/clips/{ep}/',
            f'https://www.rte.ie/radio/radio1/clips/{ep.upper()}/',
            f'https://www.rte.ie/bosco/components/player/iframe.html?clipid={ep}',
        ])

    def test_different_items_different_keys(self):
        """Different items never share an archive key (so none is wrongly skipped)."""
        urls = [
            'https://www.rte.ie/radio/rnag/clips/5f025eb2-d6d0-449a-be23-b50000d5132f/',
            'https://www.rte.ie/radio/gold/clips/34788141-b67a-4ee4-b7a0-b4b4014cb486/',
            'https://www.rte.ie/bosco/components/player/iframe.html?clipid=22640012',
            'https://www.rte.ie/media-embed/dustin/?id=3632796',
            'https://www.rte.ie/radio/radio1/news-at-one/episodes/11787175/',
        ]
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
