# yt-dlp-rte-video

A **vibe-coded** yt-dlp plugin for DRM-free video and audio on rte.ie.

## Install

Put this whole folder (the one containing `yt_dlp_plugins/`) into any of yt-dlp's [plugin locations](https://github.com/yt-dlp/yt-dlp#installing-plugins):

| Location | Path |
|---|---|
| User, Linux/macOS (recommended) | `${XDG_CONFIG_HOME:-~/.config}/yt-dlp/plugins/yt-dlp-rte-video/` |
| User, Windows (recommended) | `%APPDATA%\yt-dlp\plugins\yt-dlp-rte-video\` |
| User, other | `${XDG_CONFIG_HOME}/yt-dlp-plugins/…`, `%APPDATA%\yt-dlp-plugins\…`, `~/.yt-dlp/plugins/…`, `~/yt-dlp-plugins/…` |
| System-wide | `/etc/yt-dlp/plugins/…` or `/etc/yt-dlp-plugins/…` |
| Next to the yt-dlp executable (portable) | `<folder of yt-dlp>/yt-dlp-plugins/yt-dlp-rte-video/` |
| One run only | `yt-dlp --plugin-dirs <folder containing yt-dlp-rte-video> …` |

For example:

```bash
git clone <this repository> ~/.config/yt-dlp/plugins/yt-dlp-rte-video
```

To check it's installed, run `yt-dlp -v` with any URL. The output should include `[debug] Extractor Plugins: RteArchivesIE, RteAvgenIE, RteRadioProgrammeIE, RteVideoCategoryIE, RteVideoIE`.

## Supported URLs

| URL format | Example |
|---|---|
| `rte.ie/video/id/<id>/` | `yt-dlp https://www.rte.ie/video/id/38228/` |
| `rte.ie/video/category/<category>/` (playlist) | `yt-dlp https://www.rte.ie/video/category/News/` |
| `rte.ie/video/` (playlist of everything) | `yt-dlp https://www.rte.ie/video/` |
| `rte.ie/news/.../<id>-<slug>/` (videos in an article) | `yt-dlp https://www.rte.ie/news/courts/2026/1110/1598687-josie-ray-nenagh-trial/` |
| `rte.ie/archives/<yyyy>/<mmdd>/<id>-<slug>/` | `yt-dlp https://www.rte.ie/archives/2016/0608/794170-21st-cork-film-festival/` |
| `rte.ie/radio/<station>/clips/<id>/` | `yt-dlp https://www.rte.ie/radio/rnag/clips/5f025eb2-d6d0-449a-be23-b50000d5132f/` |
| `rte.ie/radio/<station>/<programme>/episodes/<id>/` | `yt-dlp https://www.rte.ie/radio/gold/bon-jovi-the-greatest-songs/episodes/34788141-b67a-4ee4-b7a0-b4b4014cb486/` |
| `rte.ie/radio/<station>/<programme>/` or `.../episodes/` (playlist of episodes) | `yt-dlp https://www.rte.ie/radio/radio1/morning-ireland/` |
| `rte.ie/radio/<station>/<programme>/clips/` (playlist of clips) | `yt-dlp https://www.rte.ie/radio/2fm/control/clips/` |
| `rte.ie/media-embed/dustin/?id=<id>` (embed) | `yt-dlp "https://www.rte.ie/media-embed/dustin/?id=vKr_UGcLUBDF"` |
| `rte.ie/bosco/components/player/iframe.html?clipid=<id>` (embed) | `yt-dlp "https://www.rte.ie/bosco/components/player/iframe.html?clipid=22640012"` |

DRM-protected items (RTÉ Player, live TV) are not supported. Items only available in Ireland give "This video is only available in Ireland" elsewhere.

## Tests

`tests/` holds a standard-library `unittest` suite of about 70 tests, which takes about 6 minutes to run. yt-dlp never loads it. It runs yt-dlp against this checkout only. There are two kinds of test, so none depends on a particular RTÉ item still existing:
- **Live tests** pick current items from RTÉ's own listings on every run (the News video category, News at One, Morning Ireland, the news RSS feed, the RTÉ Archives home page). They compare the plugin's results with RTÉ's data, fetched separately with `curl`.
- **Simulated tests**, each marked `SIMULATED RTÉ DATA`, replace one RTÉ response with hand-written data in RTÉ's format. They cover cases RTÉ may not have on a given day, such as the clock changes, archive captions and unusual titles.

Run it from the repository root:

```bash
python3 -m unittest discover -v -s tests
```

The tests are kept lean. They don't download media files or walk through very long listings.

Options:
- `YTDLP=<path>` chooses the yt-dlp to test (default: `/usr/local/bin/yt-dlp`). The tests that simulate RTÉ server errors and timeouts need yt-dlp's standard zipapp build, and are skipped with any other build.
- `RTE_TESTS_IN_IRELAND=1` skips the geo-block test.
- `RTE_TESTS_TIMEOUT=<seconds>` sets the time limit for each yt-dlp run (default: 600).

What it tests:
- every URL format above, and variants of programme URLs (case, trailing slash, `http`, no `www`, query string);
- metadata against RTÉ's data: title, dates (including Irish summer time), duration, station, `media_type`, release dates of archive items;
- playlists: size limits, page boundaries, order, no duplicates, `-I` ranges, date filters and `--break-on-existing`;
- one download-archive key per item, whichever URL it is reached by;
- error handling: DRM, geo-blocking, expired items, empty lists, and RTÉ server errors (injected);
- that vanilla yt-dlp still lacks support for these URLs, and that yt-dlp's own RTÉ extractors are left alone.

An optional extra check, not part of the normal run, confirms that real RTÉ data still has the shape of the simulated data, and that the situations it simulates really occur:

```bash
python3 -m unittest discover -v -s tests -p check_simulated_data.py
```

A test skipped with "RTÉ data changed" means RTÉ no longer offers that kind of item at all (for example, no more older-system radio episodes). That is not a plugin failure.
