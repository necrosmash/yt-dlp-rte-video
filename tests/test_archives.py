"""RTÉ Archives pages (rte:archives): media id from the page's embed, broadcast date from the caption.

Pinned pages with release dates read by hand from the captions; the test also checks the
caption is still on RTÉ's page, so a failure shows whether RTÉ or the plugin changed."""
import re
import unittest

from rte_helpers import RteTestCase, avgen, clean, curl, info, og

PAGES = [
    # url, release_date or None, release_year, caption phrase that must still be on the page
    ('https://www.rte.ie/archives/2016/0608/794170-21st-cork-film-festival/', '19760611', None, 'broadcast on 11 June 1976'),
    ('https://www.rte.ie/archives/2016/0806/806509-aer-lingus-launch-glamorous-new-uniform/', '19860806', None, 'broadcast on 6 August 1986'),
    # the episode's date, not the series' "first broadcast" date
    ('https://www.rte.ie/archives/2026/0920/1590621-inishbofin-ferryman/', '19860920', None, 'broadcast on 20 September 1986'),
    ('https://www.rte.ie/archives/2026/1012/1594992-patrick-and-zoe-conway/', '19961110', None, 'broadcast on 10 November 1996'),
    # archive audio: no "broadcast on" sentence, year tag 1939 -> release_year only
    ('https://www.rte.ie/archives/2014/0219/505127-ireland-to-be-neutral-in-event-of-war-de-valera/', None, 1939, None),
]


def _embed_id(html):
    m = re.search(r'rte\.ie/(?:media-embed/dustin/?\?(?:[^"\']*&)?id=|bosco/components/player/iframe\.html\?(?:[^"\']*&)?clipid=)([\w-]+)', html)
    return m.group(1) if m else None


class TestArchivePages(RteTestCase):
    def test_pages(self):
        """Archive pages: the media ID comes from the embed and the broadcast date from the caption."""
        for url, rdate, ryear, phrase in PAGES:
            with self.subTest(url=url):
                status, html = curl(url)
                self.skipIfRte(status != 200, f'{url} returned HTTP {status}')
                text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html))
                if phrase:
                    self.assertIn(phrase, text, f'RTÉ changed the caption of {url}')
                media_id = _embed_id(html)
                self.assertTrue(media_id, f'no embed found by the test on {url}')
                d, res = info(url)
                self.assertOk(res)
                self.assertRte('id', media_id.lstrip('0'), d.get('id'), 'embed on the page', url)
                self.assertRte('extractor_key', 'RteAvgen', d.get('extractor_key'), 'canonical extractor', url)
                self.assertRte('title', og(html, 'og:title'), d.get('title'), 'page og:title', url)
                self.assertRte('uploader', 'RTÉ Archives', d.get('uploader'), 'documented behaviour', url)
                self.assertRte('channel', 'RTÉ Archives', d.get('channel'), 'documented behaviour', url)
                self.assertRte('media_type', 'clip', d.get('media_type'), 'documented behaviour', url)
                self.assertRte('release_date', rdate, d.get('release_date'), f'caption {phrase!r}', url)
                if ryear:
                    self.assertRte('release_year', ryear, d.get('release_year'), 'page date_tag', url)
                    self.assertIsNone(d.get('release_timestamp'), 'archive audio got a release_timestamp (its date is an upload date)')
                elif rdate:
                    self.assertRte('release_year', int(rdate[:4]), d.get('release_year'), 'from release_date', url)
                self.assertTrue(d.get('tags'), 'keywords from the page missing')
                _, show, _ = avgen(media_id)
                if show:
                    self.assertClose('duration', show['duration'] / 1000, d.get('duration'), 0.01, 'getplaylist', url)

    def test_page_without_media(self):
        # a page id is never a media id; a page with no embed must error, not invent an item
        """A page with no embed errors instead of using the page number as a media ID."""
        status, html = curl('https://www.rte.ie/archives/')
        self.skipIfRte(status != 200, 'archives home page')
        from rte_helpers import ytdlp
        res = ytdlp('--simulate', 'https://www.rte.ie/archives/2016/0608/1-no-such-page/')
        self.assertNotEqual(res.returncode, 0)
        self.assertNoTraceback(res)


if __name__ == '__main__':
    unittest.main()
