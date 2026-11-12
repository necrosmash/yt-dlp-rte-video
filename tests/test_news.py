"""News articles: yt-dlp's generic extractor finds the Bosco (data-src) and dustin embeds through the
plugin's embed patterns. Expected media come from the article HTML (curl) and RTÉ's PID lookup."""
import html as _html
import re
import unittest

from rte_helpers import PID_LOOKUP, RteTestCase, curl, curl_json, guid_to_id, norm_id, ytdlp

ARTICLES = [
    'https://www.rte.ie/news/ireland/2026/1111/1598773-public-sector-pay-deal/',
    'https://www.rte.ie/news/dublin/2026/1110/1598665-dublin-meeting-security/',
    'https://www.rte.ie/news/courts/2026/1110/1598687-josie-ray-nenagh-trial/',
]


def _expected_keys(page):
    keys = []
    for m in re.finditer(r'(?:data-)?src="([^"]*(?:bosco/components/player/iframe\.html|media-embed/dustin/?)\?[^"]*)"', page):
        url = _html.unescape(m.group(1))
        q = dict(re.findall(r'[?&](\w+)=([^&#]*)', url))
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
    def test_articles(self):
        for url in ARTICLES:
            with self.subTest(url=url):
                status, page = curl(url)
                self.skipIfRte(status != 200, f'{url} HTTP {status}')
                expected = _expected_keys(page)
                self.skipIfRte(not expected, f'no embeds left on {url}')
                res = ytdlp('--simulate', '--ignore-no-formats-error', '--print', '%(extractor_key)s %(id)s', url)
                self.assertNoTraceback(res)
                got = [line for line in res.stdout.splitlines() if line.strip()]
                self.assertEqual(sorted(got), sorted(expected),
                                 f'embeds on the page (RTÉ HTML + PID lookup) vs plugin\n{res.describe()}')

    def test_page_id_is_never_media_id(self):
        url = ARTICLES[0]
        res = ytdlp('--simulate', '--ignore-no-formats-error', '--print', 'id', url)
        self.assertNotIn('1598773', res.stdout.split())


if __name__ == '__main__':
    unittest.main()
