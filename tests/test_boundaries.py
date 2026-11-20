"""Vanilla/built-in boundaries.

* Every URL type the plugin supports is still unsupported by vanilla yt-dlp (plugins off), so an
  upstream change is noticed.
* The plugin does not take over URLs of yt-dlp's built-in rte / rte:radio extractors.
"""
import re
import unittest

from rte_helpers import GUID_PREFIX, RteTestCase, feed_entries, programme_page, ytdlp

PINNED_URLS = {
    'news article': 'https://www.rte.ie/news/courts/2026/1110/1598687-josie-ray-nenagh-trial/',
    'archive page': 'https://www.rte.ie/archives/2016/0608/794170-21st-cork-film-festival/',
    'radio clip (numeric)': 'https://www.rte.ie/radio/radio1/clips/10507902/',
    'radio programme page': 'https://www.rte.ie/radio/radio1/morning-ireland/',
    'radio programme episodes page': 'https://www.rte.ie/radio/radio1/morning-ireland/episodes/',
    'radio programme clips page': 'https://www.rte.ie/radio/2fm/control/clips/',
    'Bosco embed (numeric)': 'https://www.rte.ie/bosco/components/player/iframe.html?clipid=22640012',
    'dustin embed (numeric)': 'https://www.rte.ie/media-embed/dustin/?id=11673745',
    'rte.ie/video category': 'https://www.rte.ie/video/category/News/',
    'rte.ie/video all': 'https://www.rte.ie/video/',
}

BUILTIN = {
    'rte': 'http://www.rte.ie/player/ie/show/iwitness-862/10478715/',
    'rte:radio': 'http://www.rte.ie/radio/utils/radioplayer/rteradioweb.html#!rii=16:10507902:2414:27-12-2015:',
}


def _dynamic_urls():
    urls = {}
    entries = feed_entries({'range': '1-30', 'byTags': 'epic:News'},
                           'guid,plprogramavailability$media.plmedia$publicUrl')
    epic = [e for e in entries if e['guid'].startswith(GUID_PREFIX)]
    if epic:
        urls['rte.ie/video clip'] = f'https://www.rte.ie/video/id/{epic[0]["guid"][len(GUID_PREFIX):]}/'
        pub = (epic[0].get('plprogramavailability$media') or [{}])[0].get('plmedia$publicUrl') or ''
        if '/media/' in pub:
            urls['dustin embed (letter id)'] = f'https://www.rte.ie/media-embed/dustin/?id={pub.rsplit("/", 1)[1]}'
    _, eps = programme_page('radio1', 'news-at-one')
    uuid_eps = [i for i in eps or [] if not str(i['id']).isdigit()]
    if uuid_eps:
        uid = uuid_eps[0]['id']
        urls['radio episode (UUID)'] = f'https://www.rte.ie/radio/radio1/news-at-one/episodes/{uid}/'
        urls['radio /clips/ URL of an episode'] = f'https://www.rte.ie/radio/radio1/clips/{uid}/'
        urls['Bosco embed (UUID)'] = f'https://www.rte.ie/bosco/components/player/iframe.html?clipid={uid}'
    _, clips = programme_page('radio1', 'morning-ireland', 'clips')
    if clips:
        urls['radio clip (UUID)'] = f'https://www.rte.ie/radio/radio1/clips/{clips[0]["id"]}/'
    return urls


class TestVanillaStillUnsupported(RteTestCase):
    """If one of these fails, upstream yt-dlp may now support the URL type: check whether the plugin still needs it."""

    @classmethod
    def setUpClass(cls):
        cls.urls = {**PINNED_URLS, **_dynamic_urls()}

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

    def test_dynamic_urls_found(self):
        """Current example URLs could be picked from RTÉ's data (otherwise the vanilla check is incomplete)."""
        missing = {'rte.ie/video clip', 'radio episode (UUID)', 'radio clip (UUID)'} - set(self.urls)
        self.assertFalse(missing, f'could not pick current URLs from RTÉ data for: {missing}')


class TestBuiltinNotTakenOver(RteTestCase):
    def test_builtin_urls_go_to_builtin_extractors(self):
        """URLs of yt-dlp's own RTÉ extractors are left to them, not taken over by the plugin."""
        for ie, url in BUILTIN.items():
            with self.subTest(ie=ie):
                res = ytdlp('--simulate', url)  # the built-ins fail on these retired URLs; that is not ours
                self.assertRegex(res.stderr, rf'\[{re.escape(ie)}\] ', f'{ie} did not handle {url}\n{res.describe()}')
                self.assertNotRegex(res.stderr, r'\[rte:(?:video|avgen|archives|radio:programme)',
                                    f'a plugin extractor claimed the built-in URL {url}\n{res.describe()}')


if __name__ == '__main__':
    unittest.main()
