# yt-dlp-rte-video

A yt-dlp plugin for DRM-free video and audio on rte.ie.

## Install

Put this whole folder (the one containing `yt_dlp_plugins/`) into any of yt-dlp's plugin locations:

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

`tests/` holds a standard-library `unittest` suite. yt-dlp never loads it. It runs yt-dlp against this checkout only. It compares the results with RTÉ's own data, fetched separately with `curl`.

Run it from the repository root:

```bash
python3 -m unittest discover -v -s tests                   # normal run, about 7 minutes
RTE_TESTS_SLOW=1 python3 -m unittest discover -v -s tests  # adds downloads and full listings
```

Options:
- `YTDLP=<path>` chooses the yt-dlp to test (default: `/usr/local/bin/yt-dlp`).
- `RTE_TESTS_IN_IRELAND=1` skips the geo-block test.

What it tests:
- every URL format above, including URL variants (case, trailing slash, query string);
- metadata against RTÉ's data: title, dates (including Irish summer time), duration, station, `media_type`, release dates of archive items;
- playlists: size limits, page boundaries, order, no duplicates, `-I` ranges, date filters and `--break-on-existing`;
- one download-archive key per item, whichever URL it is reached by;
- error handling: DRM, geo-blocking, expired items, empty lists, and RTÉ server errors (injected);
- that vanilla yt-dlp still lacks support for these URLs, and that yt-dlp's own RTÉ extractors are left alone;
- in slow mode, real downloads checked with ffprobe and ffmpeg.

A test skipped with "RTÉ data changed" means an item it relies on is gone from RTÉ. That is not a plugin failure.
