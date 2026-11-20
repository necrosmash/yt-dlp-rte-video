"""Checks first run by hand during development, which the rest of the suite did not cover.

- an expired (not just unknown) item gives RTÉ's own message
- an archive caption with two broadcast dates (series start and this episode)
- an unprotected RTÉ Player item reached through its embed (dustin letter id) still extracts
- the plugin's class names never shadow a built-in extractor's
- old numeric episodes deep in a programme's list still extract, dated as RTÉ lists them
"""
import ast
import os
import subprocess
import sys
import unittest
import zipfile

from rte_helpers import (
    GUID_PREFIX, REPO_ROOT, YTDLP, RteTestCase, avgen, clean, curl, feed_entries, info,
    norm_id, programme_page, ts_from_rte, utc_date, ytdlp,
)

PLUGIN_FILE = os.path.join(REPO_ROOT, 'yt_dlp_plugins', 'extractor', 'rte_video.py')


class TestExpiredItem(RteTestCase):
    def test_expired_episode_gives_rtes_message(self):
        # Morning Ireland 2009: listed by RTÉ once, now expired (HTTP 404 with an explanation)
        """An expired episode passes on RTÉ's own message instead of a bare HTTP 404."""
        item = '2583481'
        status, show, raw = avgen(item)
        message = (raw or {}).get('message') if isinstance(raw, dict) else None
        self.skipIfRte(status != 404 or show or not message, f'getplaylist {item}: HTTP {status}, no expiry message')
        res = ytdlp('--simulate', f'https://www.rte.ie/radio/radio1/morning-ireland/episodes/{item}/')
        self.assertErrorContains(res, 'RTÉ said:')
        self.assertIn(clean(message)[:40], res.stderr, f'RTÉ message not passed on: {message!r}\n{res.describe()}')
        self.assertNotIn('HTTP Error 404', res.stderr, 'raw 404 instead of RTÉ\'s message')


class TestArchiveTwoDates(RteTestCase):
    def test_episode_date_not_series_start(self):
        # Caption: "The first episode was broadcast on 15 April 1985." (series) and
        # "It was first broadcast on 9 November 1986." (this report); year tag 1986
        """A caption with two dates: the report's date is used, not the series' first episode."""
        url = 'https://www.rte.ie/archives/2021/1013/1253465-traditional-musician-peter-horan/'
        status, html = curl(url)
        self.skipIfRte(status != 200, f'{url} returned HTTP {status}')
        self.skipIfRte('first broadcast on 9 November 1986' not in html, 'caption changed')
        d, res = info(url)
        self.assertOk(res)
        self.assertRte('release_date', '19861109', d.get('release_date'), 'caption, read by hand', url)


class TestPlayerItemViaEmbed(RteTestCase):
    def test_unprotected_player_item_extracts(self):
        """An unprotected RTÉ Player item reached through its embed still downloads."""
        fields = ('guid,title,rte$licenceGeos,plprogramavailability$media.plmedia$publicUrl,'
                  'plprogramavailability$media.media$content.plfile$isProtected')
        entries = feed_entries({'byProgramType': 'episode', 'range': '1-300'}, fields)
        pick = None
        for e in entries:
            media = (e.get('plprogramavailability$media') or [{}])[0] or {}
            protected = any(c.get('plfile$isProtected') for c in media.get('media$content') or [])
            pid = (media.get('plmedia$publicUrl') or '').rsplit('/', 1)[-1]
            if pid and not protected and e.get('rte$licenceGeos') == 'WW' and not e['guid'].startswith(GUID_PREFIX):
                pick = e, pid
                break
        self.skipIfRte(not pick, 'no unprotected worldwide RTÉ Player episode among the newest 300')
        e, pid = pick
        url = f'https://www.rte.ie/media-embed/dustin/?id={pid}'
        d, res = info(url)
        self.assertOk(res, f'unprotected Player item {e["guid"]}: ')
        self.assertRte('extractor_key', 'RteVideo', d.get('extractor_key'), 'letter dustin id', url)
        self.assertRte('id', e['guid'], d.get('id'), 'feed guid (non-epic ids keep their full form)', url)
        self.assertTrue(d.get('formats') or d.get('url'), 'no formats')


class TestNoShadowing(RteTestCase):
    def test_plugin_class_names_not_used_by_builtins(self):
        """Plugin class names never clash with (and so replace) a built-in yt-dlp extractor."""
        if not (os.path.isfile(YTDLP) and zipfile.is_zipfile(YTDLP)):
            self.skipTest('needs $YTDLP to be the yt-dlp zipapp')
        with open(PLUGIN_FILE, encoding='utf-8') as f:
            tree = ast.parse(f.read())
        plugin = {n.name for n in tree.body if isinstance(n, ast.ClassDef) and n.name.endswith('IE')}
        self.assertTrue(plugin, 'no extractor classes found in the plugin file')
        code = ('import sys; sys.path.insert(0, sys.argv[1]);'
                'from yt_dlp.extractor import gen_extractor_classes;'
                'print("\\n".join(c.__name__ for c in gen_extractor_classes()))')
        out = subprocess.run([sys.executable, '-c', code, YTDLP], capture_output=True, text=True,
                             env={**os.environ, 'YTDLP_NO_PLUGINS': '1'}, timeout=300)
        builtin = set(out.stdout.split())
        self.assertIn('RteIE', builtin, f'built-in list not read: {out.stderr[-500:]}')
        self.assertFalse(plugin & builtin, f'plugin class names also used by built-in extractors: {plugin & builtin}')


class TestDeepOldEpisodes(RteTestCase):
    def test_old_numeric_episodes_extract(self):
        """Old episodes deep in a programme's list still extract, dated as RTÉ lists them."""
        station, programme = 'radio1', 'morning-ireland'
        checked = 0
        for page in (20, 80):
            status, items = programme_page(station, programme, 'episodes', page)
            if status != 200 or not items:
                continue
            item = next((i for i in items if str(i.get('id') or '').isdigit()), None)
            if not item:
                continue
            item_id = norm_id(item['id'])
            url = f'https://www.rte.ie/radio/{station}/{programme}/episodes/{item_id}/'
            with self.subTest(page=page, id=item_id):
                _, show, _ = avgen(item_id)
                self.skipIfRte(not show, f'{item_id} (page {page}) no longer in getplaylist')
                d, res = info(url)
                self.assertOk(res)
                self.assertRte('id', item_id, d.get('id'), f'episodes/json page {page}', url)
                self.assertRte('media_type', 'episode', d.get('media_type'), 'form long', url)
                self.assertTrue(d.get('formats') or d.get('url'), 'no formats')
                expected = utc_date(ts_from_rte(show.get('published')))
                self.assertRte('upload_date', expected, d.get('upload_date'), 'getplaylist published', url)
                checked += 1
        self.skipIfRte(not checked, 'no numeric episodes on pages 20/80 of Morning Ireland')


if __name__ == '__main__':
    unittest.main()
