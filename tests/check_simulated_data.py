"""OPTIONAL check: does real RTÉ data still look like the simulated data the tests use?

The tests marked SIMULATED RTÉ DATA replace RTÉ's responses with hand-written ones. This check
fetches real responses (plain curl, no yt-dlp) and confirms two things:
  1. shape: every field the simulated data uses also appears in real data, with the same kind of
     value, so the simulated tests still exercise what the plugin really receives;
  2. examples: the situations the simulated tests cover (clock-change times, archive captions with
     several dates, items without a station, ...) really occur in RTÉ's data.
A failure in part 1 means the simulated data needs updating. A failure in part 2 means no real
example turned up in this run's sample: the simulated test is then guarding against something not
currently seen (RTÉ's data changes daily, so try again another day before acting on it). Checks
marked "information only" are skipped, not failed, when nothing turns up: they cover cases RTÉ
does not produce every day, or at all today.

Not part of the normal run (its file name does not start with 'test'). Run it from the repository root:
    python3 -m unittest discover -v -s tests -p check_simulated_data.py
"""
import re
import unittest

from rte_helpers import (
    FEED, PID_LOOKUP, RteTestCase, avgen, curl, curl_json, embed_query, feed_entries, live_archive_pages,
    live_news_articles, live_numeric_item, live_uuid_clip, live_uuid_episode, norm_id, programme_page,
    sim_list_item, sim_show, stations,
)
from test_archives import _page_text, _sim_page
from test_programme import _sim_list
from test_video import _sim_entry, _sim_feed

REPORT = []


def note(line):
    REPORT.append(line)


def kind(value):
    """The kind of a JSON value, for comparing simulated and real fields."""
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'bool'
    if isinstance(value, (int, float)):
        return 'number'
    return type(value).__name__


def compare_fields(case, label, simulated, real_samples, optional=()):
    """Every simulated field must occur in at least one real sample, with a value of the same kind
    (or null). Fields listed in `optional` may be missing in real data (the plugin copes either way)."""
    problems = []
    for key, value in simulated.items():
        real_kinds = {kind(r[key]) for r in real_samples if key in r}
        if not real_kinds:
            if key not in optional:
                problems.append(f'{key!r} never appears in real data')
            continue
        if value is not None and kind(value) not in real_kinds | {'null'} and real_kinds != {'null'}:
            problems.append(f'{key!r} is {kind(value)} in the simulation but {sorted(real_kinds)} in real data')
    note(f'{label}: {len(simulated)} simulated fields checked against {len(real_samples)} real sample(s)'
         + (f'; PROBLEMS: {problems}' if problems else '; all match'))
    case.assertFalse(problems, f'{label}: simulated data no longer matches RTÉ: {problems}')


class Part1Shapes(RteTestCase):
    """Part 1: the simulated data has the same shape as RTÉ's real responses."""

    @classmethod
    def setUpClass(cls):
        cls.shows = {}
        ep, clip, numeric = live_uuid_episode(), live_uuid_clip(), live_numeric_item()
        if ep:
            cls.shows['newer episode'] = avgen(ep)[1]
        if clip:
            cls.shows['newer clip'] = avgen(clip)[1]
        if numeric:
            cls.shows['older episode'] = numeric[1]
        for _, _, embeds in live_archive_pages(want=2):
            ids = [embed_query(e).get('id', '') for e in embeds if 'dustin' in e]
            num = next((norm_id(i) for i in ids if i.isdigit()), None)
            if num and avgen(num)[1]:
                cls.shows.setdefault('archive item', avgen(num)[1])
        cls.shows = {k: v for k, v in cls.shows.items() if v}

    def test_getplaylist_item(self):
        """getplaylist items (sim_show) have the fields and value kinds of real ones."""
        self.assertTrue(self.shows, 'no real getplaylist items could be fetched')
        note(f'real getplaylist samples: {sorted(self.shows)}')
        sim = sim_show('99999901', form='long', episode_id='x')['shows'][0]
        # description: newer items have none (the plugin then reads the item's web page; the
        # simulation supplies one so no page is fetched). form/episode_id: only some items have them.
        compare_fields(self, 'getplaylist item', sim, list(self.shows.values()),
                       optional=('description', 'form', 'episode_id'))

    def test_list_page(self):
        """episodes/json and clips/json pages (_sim_list, sim_list_item) have the shape of real ones."""
        real_pages, real_items = [], []
        for station, programme, kind_ in (('radio1', 'news-at-one', 'episodes'), ('radio1', 'morning-ireland', 'clips')):
            status, data = curl_json(f'https://www.rte.ie/radio/{station}/{programme}/{kind_}/json/', {'page': 1})
            if isinstance(data, dict):
                real_pages.append(data)
                real_items += (data.get('programmes') or [])[:5]
        sim_item = sim_list_item('x', 'Simulated', '2026-01-01T00:00:00Z', BroadcastDateUnixTime=0)
        compare_fields(self, 'list page', _sim_list([sim_item]), real_pages)
        compare_fields(self, 'list item', sim_item, real_items)

    def test_feed_entry(self):
        """thePlatform feed answers and entries (_sim_feed, _sim_entry) have the shape of real ones."""
        status, data = curl_json(FEED, {'byProgramType': 'clip', 'sort': 'rte$broadcastDate|desc',
                                        'range': '1-5', 'byTags': 'epic:News'})
        self.assertIsInstance(data, dict, f'feed returned HTTP {status}')
        entry = _sim_entry('9999901')
        compare_fields(self, 'feed answer', _sim_feed([entry]), [data])
        compare_fields(self, 'feed entry', entry, data.get('entries') or [])
        real_media = [m for e in data.get('entries') or [] for m in e.get('plprogramavailability$media') or []]
        compare_fields(self, 'feed entry media', entry['plprogramavailability$media'][0], real_media)

    def test_pid_lookup(self):
        """The PID lookup answers {'entries': [{'guid': ...}]}, as simulated for the ID guard."""
        entries = feed_entries({'range': '1-5', 'byTags': 'epic:News'}, 'guid,plprogramavailability$media.plmedia$publicUrl')
        pid = next(((e.get('plprogramavailability$media') or [{}])[0].get('plmedia$publicUrl', '').rsplit('/', 1)[-1]
                    for e in entries), None)
        status, data = curl_json(PID_LOOKUP, {'bypid': pid})
        guid = ((data or {}).get('entries') or [{}])[0].get('guid')
        note(f'PID lookup: pid {pid} -> guid {guid}')
        self.assertTrue(guid, f'PID lookup for {pid} returned no entries[0].guid (HTTP {status})')

    def test_archive_page(self):
        """Archive pages have what the simulated pages have: og title/description, keywords, year tag,
        a dustin embed and a caption."""
        pages = live_archive_pages(want=3)
        self.assertTrue(pages, 'no archive pages found')
        sim = _sim_page('Caption.', 1986, '99999999')
        parts = {
            'og:title': r'<meta[^>]+property="og:title"', 'og:description': r'<meta[^>]+property="og:description"',
            'keywords': r'<meta[^>]+name="keywords"', 'year tag': r'<span[^>]+class="date_tag"',
            'dustin embed': r'<iframe[^>]+src="https://www\.rte\.ie/media-embed/dustin/\?id=\d+',
        }
        for name, rx in parts.items():
            self.assertRegex(sim, rx, f'simulated page lacks {name} (test bug)')
            found = sum(bool(re.search(rx, html)) for _, html, _ in pages)
            note(f'archive page {name}: in {found} of {len(pages)} real pages')
            self.assertTrue(found, f'no real archive page has {name} any more')


class Part2Examples(RteTestCase):
    """Part 2: the situations the simulated tests cover really occur in RTÉ's data."""

    def test_irish_local_times_without_zone(self):
        """Older items give Irish local time with no zone (what the clock-change tests simulate)."""
        found = live_numeric_item()
        self.assertTrue(found, 'no older-system item found')
        published = found[1].get('published')
        note(f'older item {found[1].get("itemid")} published {published!r}')
        self.assertRegex(published or '', r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$', 'older items now carry a time zone')

    def test_long_short_and_episode_id(self):
        """Older items say form long/short; newer clips name their episode in episode_id."""
        numeric = live_numeric_item()
        clip = live_uuid_clip()
        form = numeric and numeric[1].get('form')
        clip_show = clip and avgen(clip)[1]
        note(f'older item form: {form!r}; newer clip episode_id: {(clip_show or {}).get("episode_id")!r}')
        self.assertIn(form, ('long', 'short'))
        self.assertTrue(clip_show and clip_show.get('episode_id') and clip_show['episode_id'] != clip_show['itemid'])

    def test_item_not_on_a_radio_station(self):
        """Archive items are not on a radio station (no station number, or one such as 13 that is not a
        radio station), as simulated for archive audio."""
        radio = set(stations())
        seen = []
        for _, _, embeds in live_archive_pages(want=3):
            for e in embeds:
                i = embed_query(e).get('id', '')
                if i.isdigit():
                    show = avgen(norm_id(i))[1]
                    if show:
                        seen.append((show['itemid'], show.get('channelid'), show.get('channel')))
        note(f'archive items (id, station number, channel): {seen}; radio station numbers: {sorted(radio)}')
        self.assertTrue(seen, 'no archive items found')
        self.assertTrue(any(cid is None or int(cid) not in radio for _, cid, _ in seen),
                        'every sampled archive item is on a radio station')

    def test_web_exclusive_channel(self):
        """Some news videos carry 'WEB Exclusive' where the channel goes."""
        channels = set()
        for _, _, embeds in live_news_articles(want=15, scan=45):
            for e in embeds:
                q = embed_query(e)
                i = q.get('clipid') or (q.get('id') if q.get('id', '').isdigit() else None)
                if i:
                    show = avgen(norm_id(i))[1]
                    if show:
                        channels.add(show.get('channel'))
        note(f'channel values on news videos: {sorted(map(str, channels))}')
        self.assertIn('WEB Exclusive', channels, 'no news video with channel "WEB Exclusive" in this sample')

    def test_archive_caption_patterns(self):
        """Archive captions with several dates, series sentences, and no date at all all occur."""
        pages = live_archive_pages(want=25, scan=25)
        counts = {'several dates': 0, 'series/first-episode sentence': 0, 'no broadcast date': 0}
        for _, html, _ in pages:
            text = _page_text(html)
            dates = re.findall(r'broadcast on \d{1,2}(?:st|nd|rd|th)? [A-Z][a-z]+ \d{4}', text)
            counts['several dates'] += len(dates) >= 2
            counts['series/first-episode sentence'] += bool(re.search(r'first episode|series', text, re.I) and dates)
            counts['no broadcast date'] += not dates
        note(f'archive captions in {len(pages)} pages: {counts}')
        missing = [k for k, v in counts.items() if not v]
        self.assertFalse(missing, f'not seen in this sample: {missing}')

    def test_title_quirks(self):
        """Titles with stray spaces or HTML entities occur in RTÉ's lists."""
        titles = []
        for station, programme, kind_ in (('radio1', 'morning-ireland', 'clips'), ('radio1', 'news-at-one', 'episodes')):
            titles += [i.get('title') or '' for i in programme_page(station, programme, kind_, 1)[1] or []]
        titles += [e.get('title') or '' for e in feed_entries({'range': '1-200', 'byTags': 'epic:News'}, 'title')]
        odd = [t for t in titles if t != t.strip() or '  ' in t or '&' in t and ';' in t or '\x00' in t]
        note(f'titles with stray spaces/entities/NUL: {len(odd)} of {len(titles)}, e.g. {odd[:3]}')
        self.assertTrue(odd, 'no title with stray spaces, entities or NUL in this sample')

    def test_zero_runtime(self):
        """Some rte.ie/video items have a runtime of 0."""
        old = feed_entries({'range': '1-100', 'sort': 'rte$broadcastDate'}, 'guid,plprogram$runtime')
        zero = [e['guid'] for e in old if e['guid'].startswith('epic-mpx-manual-') and not e.get('plprogram$runtime')]
        note(f'oldest 100 feed clips with runtime 0: {len(zero)}, e.g. {zero[:3]}')
        self.assertTrue(zero, 'no runtime-0 item among the oldest 100 feed clips')

    @staticmethod
    def _last_page(station, programme, kind_):
        """The last page of a list (found by doubling, then halving: about 2 x log2(pages) requests),
        with its items and what the page after it gives: 'HTTP 404', 'empty list' or 'HTTP <status>'."""
        def items(page):
            status, found = programme_page(station, programme, kind_, page)
            return found if status == 200 and found else None
        if not items(1):
            return None
        lo, hi = 1, 2
        while items(hi) and hi < 4096:
            lo, hi = hi, hi * 2
        while hi - lo > 1:
            mid = (lo + hi) // 2
            lo, hi = (mid, hi) if items(mid) else (lo, mid)
        status, nxt = programme_page(station, programme, kind_, lo + 1)
        after = 'empty list' if status == 200 and nxt == [] else f'HTTP {status}'
        return lo, items(lo), after

    def test_list_ends(self):
        """A list ends the ways the simulated and fault tests assume: a short last page, or a full one
        followed by HTTP 404 or by an empty list."""
        ends = {}
        for station, programme, kind_ in (('radio1', 'news-at-one', 'episodes'), ('radio1', 'morning-ireland', 'clips')):
            found = self._last_page(station, programme, kind_)
            self.assertTrue(found, f'{programme} {kind_}: no list')
            page, last, after = found
            ends[f'{programme}/{kind_}'] = f'last page {page} has {len(last)} items; page {page + 1} gives {after}'
            self.__class__.last_items = getattr(self.__class__, 'last_items', []) + last
        note(f'list ends: {ends}')
        for name, end in ends.items():
            self.assertRegex(end, r'has \d{1,2} items|gives (?:HTTP 404|empty list)',
                             f'{name}: list ends neither short, nor with 404, nor with an empty list')

    def test_placeholder_dates(self):
        """Placeholder dates (0001-01-01, negative Unix times) for undated items (information only)."""
        if not getattr(self.__class__, 'last_items', None):
            self.test_list_ends()
        items = self.__class__.last_items
        odd = [i.get('id') for i in items if str(i.get('published', '')).startswith('0001')
               or (isinstance(i.get('BroadcastDateUnixTime'), int) and i['BroadcastDateUnixTime'] < 0)]
        note(f'items with placeholder dates among the {len(items)} oldest sampled: {odd[:3] or "none"}')
        if not odd:
            self.skipTest('information only: no placeholder date among the oldest items sampled today '
                          '(RTÉ has used them for undated clips; the simulated test keeps that case covered)')

    def test_all_digit_player_ids(self):
        """All-digit RTÉ Player IDs, which the ID prefix guard protects against (information only)."""
        entries = feed_entries({'range': '1-500'}, 'guid')
        digits = [e['guid'] for e in entries if e['guid'].isdigit()]
        note(f'all-digit non-rte.ie/video IDs in the newest 500 feed clips: {digits[:5] or "none"}')
        if not digits:
            self.skipTest('information only: RTÉ does not use all-digit Player IDs today; '
                          'the guard is a precaution and the simulated test keeps it covered')


def tearDownModule():
    print('\n==== What real RTÉ data showed ====')
    for line in REPORT:
        print(' -', line)


if __name__ == '__main__':
    unittest.main()
