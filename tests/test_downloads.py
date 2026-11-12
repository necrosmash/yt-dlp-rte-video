"""Real downloads with media checks (slow: RTE_TESTS_SLOW=1).

For each item: exit 0, file exists, expected stream types (ffprobe; MP3 cover art shows up as a
video stream with attached_pic and is ignored), duration within 2 s of RTÉ's value, size at least
100 KB and half of duration x the lowest bitrate on offer, and a clean full decode."""
import glob
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from rte_helpers import (
    GUID_PREFIX, RteTestCase, avgen, feed_entries, guid_to_id, info, programme_page, slow, ytdlp,
)


def _ffprobe(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path],
                         capture_output=True, text=True, timeout=120)
    return json.loads(out.stdout or '{}')


@slow
@unittest.skipUnless(shutil.which('ffprobe') and shutil.which('ffmpeg'), 'needs ffmpeg and ffprobe')
class TestDownloads(RteTestCase):
    def download_and_check(self, url, rte_duration, kind, ext=None, fmt=None):
        meta, res = info(url, *(['-f', fmt] if fmt else []))
        self.assertOk(res)
        rates = [f.get('tbr') for f in meta.get('formats') or [] if f.get('tbr')]
        with tempfile.TemporaryDirectory(prefix='rte-dl-') as tmp:
            res = ytdlp('-o', os.path.join(tmp, '%(id)s.%(ext)s'), *(['-f', fmt] if fmt else []), url, timeout=1800)
            self.assertOk(res)
            files = [f for f in glob.glob(os.path.join(tmp, '*')) if not f.endswith(('.part', '.ytdl'))]
            self.assertEqual(len(files), 1, f'files: {files}')
            path = files[0]
            if ext:
                self.assertTrue(path.endswith('.' + ext), f'{path}: expected .{ext}')
            probe = _ffprobe(path)
            streams = [s for s in probe.get('streams', []) if not s.get('disposition', {}).get('attached_pic')]
            types = sorted({s['codec_type'] for s in streams})
            self.assertEqual(types, ['audio'] if kind == 'audio' else ['audio', 'video'], f'{path}: streams {types}')
            duration = float(probe['format']['duration'])
            self.assertLessEqual(abs(duration - rte_duration), 2, f'file {duration:.2f} s vs RTÉ {rte_duration:.2f} s')
            size = os.path.getsize(path)
            minimum = max(100_000, (rte_duration * min(rates) * 1000 / 8) / 2 if rates else 0)
            self.assertGreaterEqual(size, minimum, f'{path}: {size} bytes < {minimum:.0f}')
            dec = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-f', 'null', '-'],
                                 capture_output=True, text=True, timeout=1800)
            self.assertEqual((dec.returncode, dec.stderr.strip()), (0, ''), f'decode errors in {path}')

    def test_rte_video_clip(self):
        entries = feed_entries({'range': '1-20', 'byTags': 'epic:News'},
                               'guid,plprogram$runtime,rte$licenceGeos,plprogramavailability$media.media$content.plfile$isProtected')
        ok = [e for e in entries if e['guid'].startswith(GUID_PREFIX) and e.get('rte$licenceGeos') == 'WW'
              and float(e.get('plprogram$runtime') or 0) > 0]
        self.skipIfRte(not ok, 'no WW News clip')
        e = min(ok, key=lambda x: float(x['plprogram$runtime']))
        self.download_and_check(f'https://www.rte.ie/video/id/{guid_to_id(e["guid"])}/', float(e['plprogram$runtime']),
                                'video', fmt='wv*+wa/w')

    def test_bosco_news_video(self):
        status, show, _ = avgen('22640012')
        self.skipIfRte(not show, 'item 22640012 gone')
        self.download_and_check('https://www.rte.ie/bosco/components/player/iframe.html?clipid=22640012',
                                show['duration'] / 1000, 'video', 'mp4', fmt='w')

    def test_radio_clip_mp3(self):
        _, clips = programme_page('radio1', 'morning-ireland', 'clips')
        self.skipIfRte(not clips, 'no Morning Ireland clips')
        c = min(clips[:10], key=lambda x: x.get('duration') or 1e12)
        _, show, _ = avgen(c['id'])
        self.download_and_check(f'https://www.rte.ie/radio/radio1/clips/{c["id"]}/', show['duration'] / 1000, 'audio', 'mp3')

    def test_numeric_audio_hls_is_m4a(self):
        status, show, _ = avgen('3632796')
        self.skipIfRte(not show, 'item 3632796 gone')
        self.download_and_check('https://www.rte.ie/media-embed/dustin/?id=3632796', show['duration'] / 1000, 'audio', 'm4a')

    def test_archive_video(self):
        status, show, _ = avgen('10603799')
        self.skipIfRte(not show, 'item 10603799 gone')
        self.download_and_check('https://www.rte.ie/archives/2016/0806/806509-aer-lingus-launch-glamorous-new-uniform/',
                                show['duration'] / 1000, 'video', 'mp4', fmt='w')


if __name__ == '__main__':
    unittest.main()
