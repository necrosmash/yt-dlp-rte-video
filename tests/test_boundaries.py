"""Vanilla/built-in boundaries.

* Every URL type the plugin supports is still unsupported by vanilla yt-dlp (plugins off), so an
  upstream change is noticed. The example URLs are current ones picked from RTÉ's own listings.
* The plugin does not take over URLs of yt-dlp's built-in rte / rte:radio extractors, and its
  class names never replace a built-in extractor.
"""
import ast
import os
import re
import subprocess
import sys
import unittest
import zipfile

from rte_helpers import (
    GUID_PREFIX, REPO_ROOT, YTDLP, RteTestCase, embed_query, feed_entries, live_archive_pages,
    live_news_articles, live_numeric_item, live_uuid_clip, live_uuid_episode, norm_id, ytdlp,
)

# Programme pages of RTÉ's long-running flagship programmes (the playlist URL types)
PROGRAMME_URLS = {
    'radio programme page': 'https://www.rte.ie/radio/radio1/morning-ireland/',
    'radio programme episodes page': 'https://www.rte.ie/radio/radio1/morning-ireland/episodes/',
    'radio programme clips page': 'https://www.rte.ie/radio/radio1/morning-ireland/clips/',
    'rte.ie/video category': 'https://www.rte.ie/video/category/News/',
    'rte.ie/video all': 'https://www.rte.ie/video/',
}

# URL formats of yt-dlp's built-in RTÉ extractors; matched by URL alone, so they never expire
BUILTIN = {
    'rte': 'http://www.rte.ie/player/ie/show/iwitness-862/10478715/',
    'rte:radio': 'http://www.rte.ie/radio/utils/radioplayer/rteradioweb.html#!rii=16:10507902:2414:27-12-2015:',
}

PLUGIN_FILE = os.path.join(REPO_ROOT, 'yt_dlp_plugins', 'extractor', 'rte_video.py')


def _live_urls():
    urls = dict(PROGRAMME_URLS)
    entries = feed_entries({'range': '1-30', 'byTags': 'epic:News'},
                           'guid,plprogramavailability$media.plmedia$publicUrl')
    epic = [e for e in entries if e['guid'].startswith(GUID_PREFIX)]
    if epic:
        urls['rte.ie/video clip'] = f'https://www.rte.ie/video/id/{epic[0]["guid"][len(GUID_PREFIX):]}/'
        pub = (epic[0].get('plprogramavailability$media') or [{}])[0].get('plmedia$publicUrl') or ''
        if '/media/' in pub:
            urls['dustin embed (letter id)'] = f'https://www.rte.ie/media-embed/dustin/?id={pub.rsplit("/", 1)[1]}'
    ep = live_uuid_episode()
    if ep:
        urls['radio episode (UUID)'] = f'https://www.rte.ie/radio/radio1/news-at-one/episodes/{ep}/'
        urls['radio /clips/ URL of an episode'] = f'https://www.rte.ie/radio/radio1/clips/{ep}/'
        urls['Bosco embed (UUID)'] = f'https://www.rte.ie/bosco/components/player/iframe.html?clipid={ep}'
    clip = live_uuid_clip()
    if clip:
        urls['radio clip (UUID)'] = f'https://www.rte.ie/radio/radio1/clips/{clip}/'
    numeric = live_numeric_item()
    if numeric:
        urls['radio clip (numeric)'] = f'https://www.rte.ie/radio/radio1/clips/{norm_id(numeric[0]["id"])}/'
    articles = live_news_articles(want=1)
    if articles:
        urls['news article'] = articles[0][0]
    pages = live_archive_pages(want=1)
    if pages:
        urls['archive page'] = pages[0][0]
        ids = [embed_query(e).get('id', '') for e in pages[0][2]]
        num = next((i for i in ids if i.isdigit() and len(i) >= 4), None)
        if num:
            urls['dustin embed (numeric)'] = f'https://www.rte.ie/media-embed/dustin/?id={num}'
            urls['Bosco embed (numeric)'] = f'https://www.rte.ie/bosco/components/player/iframe.html?clipid={num}'
    return urls


class TestVanillaStillUnsupported(RteTestCase):
    """If one of these fails, upstream yt-dlp may now support the URL type: check whether the plugin still needs it."""

    @classmethod
    def setUpClass(cls):
        cls.urls = _live_urls()

    def test_vanilla_unsupported(self):
        """yt-dlp without plugins still lacks these URLs; if this fails, yt-dlp may now support them itself."""
        for kind, url in self.urls.items():
            with self.subTest(kind=kind, url=url):
                res = ytdlp('--simulate', '--flat-playlist', '-I', '1', url, plugins=False)
                self.assertNoTraceback(res)
                found_nothing = 'Unsupported URL' in res.stderr or (
                    res.returncode != 0 and re.search(r'No video|no video formats|No media', res.stderr, re.I))
                self.assertTrue(
                    found_nothing,
                    f'vanilla yt-dlp (plugins off) no longer says Unsupported URL for this {kind}: '
                    f'upstream may support it now\n{res.describe()}')
                # and no built-in extractor claims it
                self.assertNotRegex(res.stderr, r'\[rte(?::radio)?\] ', f'built-in rte extractor claimed {url}')

    def test_live_urls_found(self):
        """Current example URLs of every supported kind could be picked from RTÉ's listings."""
        wanted = {'rte.ie/video clip', 'dustin embed (letter id)', 'radio episode (UUID)', 'radio clip (UUID)',
                  'radio clip (numeric)', 'news article', 'archive page', 'dustin embed (numeric)'}
        missing = wanted - set(self.urls)
        self.skipIfRte(missing, f'no current example could be found for: {sorted(missing)}')


class TestBuiltinNotTakenOver(RteTestCase):
    def test_builtin_urls_go_to_builtin_extractors(self):
        """URLs of yt-dlp's own RTÉ extractors are left to them, not taken over by the plugin."""
        for ie, url in BUILTIN.items():
            with self.subTest(ie=ie):
                res = ytdlp('--simulate', url)  # the built-ins fail on these retired URLs; that is not ours
                self.assertRegex(res.stderr, rf'\[{re.escape(ie)}\] ', f'{ie} did not handle {url}\n{res.describe()}')
                self.assertNotRegex(res.stderr, r'\[rte:(?:video|avgen|archives|radio:programme)',
                                    f'a plugin extractor claimed the built-in URL {url}\n{res.describe()}')

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


if __name__ == '__main__':
    unittest.main()
