"""News articles: yt-dlp's generic extractor finds the Bosco (data-src) and dustin embeds through the
plugin's embed patterns. Articles are current ones from RTÉ's news RSS feed; expected media come from
the article HTML (curl) and RTÉ's PID lookup."""
import re
import unittest

from rte_helpers import (
    PID_LOOKUP, RteTestCase, curl_json, embed_query, guid_to_id, live_news_articles, norm_id, ytdlp,
)


def _expected_keys(embeds):
    keys = []
    for url in embeds:
        q = embed_query(url)
        if 'bosco' in url and q.get('clipid'):
            keys.append(f'RteAvgen {norm_id(q["clipid"])}')
        elif q.get('id', '').isdigit() and len(q['id']) >= 4:
            keys.append(f'RteAvgen {norm_id(q["id"])}')
        elif q.get('id'):
            status, data = curl_json(PID_LOOKUP, {'bypid': q['id']})
            guid = ((data or {}).get('entries') or [{}])[0].get('guid')
            keys.append(f'RteVideo {guid_to_id(guid)}' if guid else f'unresolved {q["id"]}')
    return list(dict.fromkeys(keys))


class TestNewsArticles(RteTestCase):
    @classmethod
    def setUpClass(cls):
        cls.articles = live_news_articles(want=3)
        if not cls.articles:
            raise unittest.SkipTest('RTÉ data changed: no article with embedded video in the news RSS feed')

    def test_articles(self):
        """Every RTÉ video embedded in a current news article is found, and nothing else."""
        for url, _, embeds in self.articles:
            with self.subTest(url=url):
                expected = _expected_keys(embeds)
                res = ytdlp('--simulate', '--ignore-no-formats-error', '--print', '%(extractor_key)s %(id)s', url)
                self.assertNoTraceback(res)
                # other sites' embeds (e.g. YouTube) are yt-dlp's own business; compare RTÉ's only
                got = [line for line in res.stdout.splitlines() if line.startswith('Rte')]
                self.assertEqual(sorted(got), sorted(expected),
                                 f'embeds on the page (RTÉ HTML + PID lookup) vs plugin\n{res.describe()}')

    def test_page_id_is_never_media_id(self):
        """The article's own page number is never used as a media ID."""
        for url, _, _ in self.articles:
            with self.subTest(url=url):
                page_id = re.search(r'/(\d+)-[\w-]+/?$', url).group(1)
                res = ytdlp('--simulate', '--ignore-no-formats-error', '--print', 'id', url)
                self.assertNotIn(page_id, res.stdout.split())


if __name__ == '__main__':
    unittest.main()
