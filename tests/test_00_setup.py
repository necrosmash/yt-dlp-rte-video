"""The suite tests this checkout, and yt-dlp sees the plugin's extractors under their final names."""
import os
import re

from rte_helpers import PLUGIN_PARENT, REPO_ROOT, YTDLP, RteTestCase, ytdlp

PLUGIN_CLASSES = {'RteArchivesIE', 'RteAvgenIE', 'RteRadioProgrammeIE', 'RteVideoCategoryIE', 'RteVideoIE'}
# A URL yt-dlp rejects before any network request; -v prints the plugin header first
NO_NETWORK_URL = 'file:///nonexistent-rte-test'


def _debug_header(**kw):
    res = ytdlp('-v', '--simulate', NO_NETWORK_URL, **kw)
    plugins = re.search(r'\[debug\] Extractor Plugins: (.*)', res.stderr)
    dirs = re.search(r'\[debug\] Plugin directories: (.*)', res.stderr)
    return res, (set(p.strip() for p in plugins.group(1).split(',')) if plugins else set()), (dirs.group(1) if dirs else '')


class TestSetup(RteTestCase):
    def test_ytdlp_exists(self):
        """The yt-dlp being tested exists (set $YTDLP to test another one)."""
        self.assertTrue(os.path.isfile(YTDLP), f'yt-dlp not found at {YTDLP} (set $YTDLP)')

    def test_nothing_inside_plugin_package_but_plugin_code(self):
        # yt-dlp imports every module in yt_dlp_plugins/extractor: test files must never be there
        """yt-dlp imports every module in yt_dlp_plugins/extractor, so nothing else may live there."""
        ext_dir = os.path.join(REPO_ROOT, 'yt_dlp_plugins', 'extractor')
        stray = sorted(f for f in os.listdir(ext_dir) if f.endswith('.py') and f != 'rte_video.py')
        self.assertEqual(stray, [], 'unexpected modules inside yt_dlp_plugins/extractor (yt-dlp imports them all)')

    def test_loads_this_checkout_only(self):
        """Only this checkout is loaded, even when other plugins sit next to it."""
        res, plugins, dirs = _debug_header()
        self.assertEqual(plugins, PLUGIN_CLASSES, f'plugin extractor classes\n{res.describe()}')
        # exactly one plugin directory, and it is this checkout (reached through the suite's link)
        listed = [d.strip() for d in dirs.split(',') if d.strip()]
        self.assertEqual(len(listed), 1, f'plugin directories should be only this checkout: {listed}\n{res.describe()}')
        self.assertEqual(os.path.realpath(listed[0]), os.path.realpath(os.path.join(REPO_ROOT, 'yt_dlp_plugins')),
                         f'plugin directory is not this checkout\n{res.describe()}')
        self.assertTrue(listed[0].startswith(PLUGIN_PARENT), f'not loaded through the suite folder: {listed[0]}')

    def test_no_plugins_without_plugin_dirs(self):
        """With plugins switched off the RTÉ extractors are absent (so the vanilla checks mean something)."""
        res, plugins, dirs = _debug_header(plugins=False)
        self.assertFalse(plugins & PLUGIN_CLASSES,
                         f'plugin classes loaded with --no-plugin-dirs: installed elsewhere (pip/PYTHONPATH)?\n{res.describe()}')

    def test_extractor_descriptions(self):
        """yt-dlp's built-in rte and rte:radio extractors are still listed; the old name rte:clip is gone."""
        res = ytdlp('--list-extractors')
        self.assertOk(res)
        names = res.stdout.split()
        self.assertIn('rte', names)
        self.assertIn('rte:radio', names)
        self.assertNotIn('rte:clip', names, 'old extractor name rte:clip present')
