"""RTÉ Archives pages (rte:archives): media id from the page's embed, broadcast date from the caption.

The live test checks current pages from the RTÉ Archives home page. The simulated tests check how
the broadcast date is read from captions written the ways RTÉ writes them."""
import re
import unittest

from rte_helpers import (
    RteTestCase, avgen, embed_query, info, live_archive_pages, norm_id, og, sim_show, simulate, ytdlp,
)

MONTHS = 'January February March April May June July August September October November December'.split()
GETPLAYLIST = r'rteavgen/getplaylist/\?(?:.*&)?id={}(?:&|$)'


def _page_text(html):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html))


def _sim_page(caption, year_tag, item_id):
    """An archive page in the shape of RTÉ's: og/keywords meta, the year tag, the caption, a dustin embed."""
    return f'''<!DOCTYPE html><html><head>
<meta property="og:title" content="Simulated archive page">
<meta property="og:description" content="A simulated RTÉ Archives page.">
<meta name="keywords" content="Simulated, Archives">
</head><body><article>
<span class="date_tag">{year_tag}</span>
<iframe src="https://www.rte.ie/media-embed/dustin/?id={item_id}&thumbnail=00000000&subheader=off"></iframe>
<p>{caption}</p>
</article></body></html>'''


class TestLiveArchivePages(RteTestCase):
    def test_pages(self):
        """Current archive pages: the media ID comes from the embed, and any date matches the caption."""
        pages = live_archive_pages()
        self.skipIfRte(not pages, 'no archive pages with an embed on the RTÉ Archives home page')
        for url, html, embeds in pages:
            q = embed_query(embeds[0])
            if not (q.get('id', '').isdigit() and len(q['id']) >= 4) or len(embeds) != 1:
                continue
            with self.subTest(url=url):
                d, res = info(url)
                self.assertOk(res)
                self.assertRte('id', norm_id(q['id']), d.get('id'), 'embed on the page', url)
                self.assertRte('extractor_key', 'RteAvgen', d.get('extractor_key'), 'canonical extractor', url)
                self.assertRte('title', og(html, 'og:title'), d.get('title'), 'page og:title', url)
                self.assertRte('uploader', 'RTÉ Archives', d.get('uploader'), 'documented behaviour', url)
                self.assertRte('media_type', 'clip', d.get('media_type'), 'documented behaviour', url)
                text = _page_text(html)
                year = re.search(r'class="date_tag"[^>]*>\s*(\d{4})', html)
                if d.get('release_date'):
                    rd = d['release_date']
                    phrase = f'broadcast on {int(rd[6:])} {MONTHS[int(rd[4:6]) - 1]} {rd[:4]}'
                    self.assertRegex(text, rf'broadcast on {int(rd[6:])}(?:st|nd|rd|th)? {MONTHS[int(rd[4:6]) - 1]} {rd[:4]}',
                                     f'release_date {rd} is not a "{phrase}" date in the caption')
                    if year:
                        self.assertEqual(rd[:4], year.group(1), 'release_date year differs from the page year tag')
                elif year:
                    self.assertRte('release_year', int(year.group(1)), d.get('release_year'), 'page year tag', url)
                _, show, _ = avgen(norm_id(q['id']))
                if show:
                    self.assertClose('duration', show['duration'] / 1000, d.get('duration'), 0.01, 'getplaylist', url)

    def test_page_without_media(self):
        """A page with no embed errors instead of using the page number as a media ID."""
        res = ytdlp('--simulate', 'https://www.rte.ie/archives/2016/0608/1-no-such-page/')
        self.assertNotEqual(res.returncode, 0)
        self.assertNoTraceback(res)


class TestSimulatedCaptions(RteTestCase):
    def caption_info(self, page_id, caption, year_tag):
        item_id = f'9999{page_id}'
        url = f'https://www.rte.ie/archives/2099/0101/{page_id}-simulated-archive-page/'
        d, res = info(url, faults=[
            simulate(re.escape(url), _sim_page(caption, year_tag, item_id), content_type='text/html; charset=utf-8'),
            simulate(GETPLAYLIST.format(item_id), sim_show(
                item_id, channelid='13', channel='WEB Exclusive', provider='RTÉ', medium='video', ptitle='RTÉ Archives')),
        ])
        self.assertOk(res)
        self.assertEqual(d.get('title'), 'Simulated archive page')
        self.assertEqual(d.get('tags'), ['Simulated', 'Archives'])
        return d

    # SIMULATED RTÉ DATA: RTÉ's response is replaced with hand-written data in RTÉ's format as seen
    # in November 2026. If RTÉ changes that format, this test can still pass while the plugin fails
    # on real data; update the simulated data to match RTÉ's new format.
    def test_episode_date_not_series_start(self):
        """A caption with two dates: the report's date is used, not the date the series began."""
        d = self.caption_info('9101', 'The series began in 1985. The first episode was broadcast on 15 April 1985. '
                                      'This report was first broadcast on 9 November 1986.', 1986)
        self.assertEqual(d.get('release_date'), '19861109')

    # SIMULATED RTÉ DATA: RTÉ's response is replaced with hand-written data in RTÉ's format as seen
    # in November 2026. If RTÉ changes that format, this test can still pass while the plugin fails
    # on real data; update the simulated data to match RTÉ's new format.
    def test_series_sentence_loses_to_episode_sentence(self):
        """A sentence about the series' first broadcast loses to one about this episode, even in the same year."""
        d = self.caption_info('9102', 'An episode of Ireland\'s Eye broadcast on 20 September 1986. '
                                      'The series was first broadcast on 15 February 1986.', 1986)
        self.assertEqual(d.get('release_date'), '19860920')

    # SIMULATED RTÉ DATA: RTÉ's response is replaced with hand-written data in RTÉ's format as seen
    # in November 2026. If RTÉ changes that format, this test can still pass while the plugin fails
    # on real data; update the simulated data to match RTÉ's new format.
    def test_year_only_when_no_broadcast_date(self):
        """Without a 'broadcast on' date, only the page's year tag is used (a full date would be invented)."""
        d = self.caption_info('9103', 'Éamon de Valera speaks about Irish neutrality.', 1939)
        self.assertIsNone(d.get('release_date'))
        self.assertEqual(d.get('release_year'), 1939)

    # SIMULATED RTÉ DATA: RTÉ's response is replaced with hand-written data in RTÉ's format as seen
    # in November 2026. If RTÉ changes that format, this test can still pass while the plugin fails
    # on real data; update the simulated data to match RTÉ's new format.
    def test_date_from_another_year_ignored(self):
        """A 'broadcast on' date in a different year from the page's year tag is about something else."""
        d = self.caption_info('9104', 'A follow-up was broadcast on 5 May 1990.', 1986)
        self.assertIsNone(d.get('release_date'))
        self.assertEqual(d.get('release_year'), 1986)


if __name__ == '__main__':
    unittest.main()
