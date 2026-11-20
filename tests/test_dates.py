"""Dates around Irish summer-time changes (older lookup items carry Irish local time without a zone).

Pinned items found in RTÉ's ID sequence; expected UTC values are written out by hand and also
recomputed from RTÉ's live string with Europe/Dublin, so a failure shows which side changed.
Summer time: 01:00 UTC on the last Sunday of March to 01:00 UTC on the last Sunday of October."""
import datetime as dt
import unittest

from rte_helpers import RteTestCase, avgen, info, stations, ts_from_rte, utc_date


def _utc(*args):
    return int(dt.datetime(*args, tzinfo=dt.timezone.utc).timestamp())


# id, RTÉ local string, hand-computed UTC
EDGES = [
    ('11786586', '2026-03-29T00:00:00', _utc(2026, 3, 29, 0, 0)),    # change day, before 01:00 UTC: GMT
    ('11786587', '2026-03-29T02:00:00', _utc(2026, 3, 29, 1, 0)),    # first hour of summer time
    ('11757095', '2025-10-25T23:59:00', _utc(2025, 10, 25, 22, 59)),  # last evening of summer time
    ('11757097', '2025-10-26T06:00:00', _utc(2025, 10, 26, 6, 0)),   # morning after the change back: GMT
    ('11787175', '2026-04-01T13:00:00', _utc(2026, 4, 1, 12, 0)),    # ordinary summer item
    ('10507902', '2015-12-27T08:00:00', _utc(2015, 12, 27, 8, 0)),   # ordinary winter item
]


class TestSummerTimeEdges(RteTestCase):
    def test_edges(self):
        """Items just before and after the Irish clock changes get the right UTC time."""
        st = stations()
        for item_id, local, expected in EDGES:
            with self.subTest(id=item_id, local=local):
                status, show, _ = avgen(item_id)
                self.skipIfRte(not show, f'{item_id} gone (HTTP {status})')
                self.assertEqual(show.get('published'), local, f'RTÉ changed the published string of {item_id}')
                self.assertEqual(ts_from_rte(local), expected, 'test helper disagrees with hand-computed UTC')
                slug = st.get(int(show['channelid']), ('radio1',))[0] if show.get('channelid') else 'radio1'
                url = f'https://www.rte.ie/radio/{slug}/clips/{item_id}/'
                d, res = info(url)
                self.assertOk(res)
                self.assertRte('timestamp', expected, d.get('timestamp'), f'published {local!r} (Irish local)', url)
                self.assertRte('upload_date', utc_date(expected), d.get('upload_date'), 'UTC date', url)
                if d.get('media_type') == 'episode':
                    self.assertRte('release_timestamp', expected, d.get('release_timestamp'), 'broadcast time', url)


if __name__ == '__main__':
    unittest.main()
