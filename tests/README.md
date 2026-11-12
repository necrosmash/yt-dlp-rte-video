# Tests

Live tests for the plugin against rte.ie. They catch both plugin regressions and changes on RTÉ's side.

## Running

From the repository root (the folder that holds `.git` and `yt_dlp_plugins/`):

```sh
python3 -m unittest discover -s tests            # default run, about 5 minutes
RTE_TESTS_SLOW=1 python3 -m unittest discover -s tests   # also downloads and full listings
python3 -m unittest discover -s tests -p 'test_programme.py' -v   # one file, verbose
```

You need Python 3.9 or later (standard library only), `curl`, and yt-dlp. Slow mode also needs `ffmpeg` and `ffprobe`.

## How it works

- **yt-dlp runs as a separate program.** Every run uses `--ignore-config --no-cache-dir --no-plugin-dirs --plugin-dirs <parent of this checkout>`, so it loads this checkout's plugin and nothing else. The user's config and any installed copy of the plugin are ignored. `test_00_setup.py` checks this.
- **When `$YTDLP` is the yt-dlp zipapp** (the usual `/usr/local/bin/yt-dlp`), the tests start it as `python3 -c <wrapper>`. The wrapper calls yt-dlp's own `main()` unchanged, but it also:
  - counts requests per host (the totals are printed at the end of the run);
  - can inject a fault for one URL pattern: an HTTP status or a timeout. The tests use this for a mid-list HTTP 500, retries, and a 404 after a full page.

  If `$YTDLP` is not a zipapp, it runs directly. The fault-injection tests are then skipped.
- **Expected values come from RTÉ's raw data, fetched with plain `curl`.** The sources are the thePlatform feed, `rteavgen/getplaylist`, the radio `episodes/json` and `clips/json` lists, `live_stations/json`, and the pages themselves. The plugin's code is never imported. RTÉ answers Python's default user agent with HTTP 403, and the tests do not fake a browser user agent.
- **Content that rotates is picked from RTÉ's current data.** Examples are the newest clips and the first page of a programme. Pinned items are used only where a fixed known value is the point of the test, such as archive broadcast dates, summer-time edges or known regressions.
- **Failure messages show both sides**, as "`<field>: RTÉ (<source>) gives X; the plugin returned Y`". When RTÉ's own data has changed so that a test no longer applies (a pinned item has gone, or a list that should be empty has items), the test is **skipped** with "RTÉ data changed: …" rather than failed.
- Temporary files go in temp directories and are removed afterwards.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `YTDLP` | `/usr/local/bin/yt-dlp` | The yt-dlp to test with |
| `RTE_TESTS_SLOW` | unset | `1` turns on downloads with ffprobe/ffmpeg checks, full listings (World Report, all of rte.ie/video) and the oldest Lifestyle entries |
| `RTE_TESTS_IN_IRELAND` | unset | `1` skips the geo-block test, which assumes a location outside Ireland |
| `RTE_TESTS_TIMEOUT` | `600` | Per-run timeout in seconds for yt-dlp |

## Load on RTÉ

- **Default run:** a few hundred requests, mostly small JSON lookups.
- **Slow mode adds:**
  - five downloads (a few MB to a few tens of MB each);
  - World Report in full (about 15 list pages, fetched twice: once by the test, once by yt-dlp);
  - rte.ie/video in full (about 10 feed pages, fetched twice).
- **Gap filling** (`rteradioprogramme:fill_gaps=yes`) is never switched on. It can make thousands of lookups. One test checks that a default listing crossing item 100 makes no `rteavgen` lookups.

## Files

| File | What it covers |
|---|---|
| `test_00_setup.py` | Only this checkout is loaded; extractor names |
| `test_boundaries.py` | Vanilla yt-dlp still says Unsupported URL for every plugin URL type; built-in `rte` / `rte:radio` URLs stay with the built-ins |
| `test_video.py` | rte.ie/video clips, categories and the all-items listing; geo, DRM, ID guard, Channels.sh fields, the 1,000-item cap, `duration` 0 |
| `test_avgen.py` | Radio clips and episodes, Bosco and dustin embeds; `media_type` rule; station from `channelid`; archive keys across routes |
| `test_archives.py` | Archive pages: media id, broadcast date (B2), archive audio with year only (N1) |
| `test_programme.py` | Programme playlists: entries against RTÉ's lists, page boundary, no gap filling by default, URL variants, `/clips/` lists, empty-list errors, injected 500/404/timeout |
| `test_dates.py` | Irish summer-time edges (March and October changes) |
| `test_downloads.py` | Slow only: downloads and media checks |
| `test_dev_checks.py` | Checks first run by hand during development: expired item message, archive caption with two dates, unprotected RTÉ Player item via embed, no class-name clash with built-ins, old episodes deep in a list; slow: a news-script style run (break options, match filter, mkv, archive) run twice |
| `rte_helpers.py` | Shared helpers (not a test module) |
