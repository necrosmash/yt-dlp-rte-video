"""Dates around Irish summer-time changes.

RTÉ's older lookup gives times as Irish local time without a zone (newer items carry 'Z'). Irish
summer time runs from 01:00 UTC on the last Sunday of March to 01:00 UTC on the last Sunday of
October. Expected UTC values are written out by hand and also recomputed with Europe/Dublin."""
import datetime as dt
import unittest

from rte_helpers import RteTestCase, info, sim_show, simulate, ts_from_rte, utc_date

GETPLAYLIST = r'rteavgen/getplaylist/\?(?:.*&)?id={}(?:&|$)'


def _utc(*args):
    return int(dt.datetime(*args, tzinfo=dt.timezone.utc).timestamp())


# simulated item id, RTÉ published string, hand-computed UTC
EDGES = [
    ('99999911', '2026-03-29T00:00:00', _utc(2026, 3, 29, 0, 0)),    # change day, before 01:00 UTC: GMT
    ('99999912', '2026-03-29T02:00:00', _utc(2026, 3, 29, 1, 0)),    # first hour of summer time
    ('99999913', '2026-10-24T23:59:00', _utc(2026, 10, 24, 22, 59)),  # last evening of summer time
    ('99999914', '2026-10-25T06:00:00', _utc(2026, 10, 25, 6, 0)),   # morning after the change back: GMT
    ('99999915', '2026-07-01T13:00:00', _utc(2026, 7, 1, 12, 0)),    # ordinary summer item
    ('99999916', '2026-12-27T08:00:00', _utc(2026, 12, 27, 8, 0)),   # ordinary winter item
    ('99999917', '2026-07-01T13:00:00Z', _utc(2026, 7, 1, 13, 0)),   # newer items: UTC marker taken as such
    ('99999918', '2026-07-01T13:00:00+01:00', _utc(2026, 7, 1, 12, 0)),  # explicit offset taken as such
]


class TestSummerTimeEdges(RteTestCase):
    # SIMULATED RTÉ DATA: RTÉ's response is replaced with hand-written data in RTÉ's format as seen
    # in November 2026. If RTÉ changes that format, this test can still pass while the plugin fails
    # on real data; update the simulated data to match RTÉ's new format.
    def test_edges(self):
        """Items just before and after the Irish clock changes get the right UTC time."""
        for item_id, published, expected in EDGES:
            with self.subTest(published=published):
                self.assertEqual(ts_from_rte(published), expected, 'test helper disagrees with hand-computed UTC')
                url = f'https://www.rte.ie/radio/radio1/clips/{item_id}/'
                d, res = info(url, faults=[simulate(GETPLAYLIST.format(item_id),
                                                    sim_show(item_id, published=published, form='long'))])
                self.assertFaultHit(res)
                self.assertOk(res)
                self.assertRte('timestamp', expected, d.get('timestamp'), f'published {published!r}', url)
                self.assertRte('upload_date', utc_date(expected), d.get('upload_date'), 'UTC date', url)
                self.assertRte('release_timestamp', expected, d.get('release_timestamp'), 'broadcast time', url)


if __name__ == '__main__':
    unittest.main()
