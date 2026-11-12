"""
yt-dlp plugin: DRM-free video and audio from rte.ie.

Install: <plugins dir>/yt-dlp-rte-video/yt_dlp_plugins/extractor/rte_video.py
  e.g. ~/.yt-dlp/plugins/yt-dlp-rte-video/yt_dlp_plugins/extractor/rte_video.py

Supported URLs
  https://www.rte.ie/video/id/38228/                                rte.ie/video clip
  https://www.rte.ie/video/category/News/                           rte.ie/video category (playlist)
  https://www.rte.ie/video/                                         all of rte.ie/video (playlist)
  https://www.rte.ie/news/<section>/<yyyy>/<mmdd>/<id>-<slug>/      news article video(s), via embeds
  https://www.rte.ie/archives/<yyyy>/<mmdd>/<id>-<slug>/            RTE Archives clip, with broadcast date
  https://www.rte.ie/radio/<station>/clips/<id>/                    radio clip (audio)
  https://www.rte.ie/radio/<station>/<programme>/episodes/<id>/     radio episode (audio)
  https://www.rte.ie/radio/<station>/<programme>/                   radio programme (playlist of episodes)

Items RTE marks as DRM-protected are reported as DRM and not downloaded.
RTE Player (/player/...) and live TV use DRM and are not supported.
Geo-restriction is handled like yt-dlp's SVT extractor: Ireland is named in the
error, and no fake IP is sent unless the user asks for it with --xff.

Experimental: radio programme playlists fill gaps in RTE's own episode list
(see RteRadioProgrammeIE._fill_gap). Disable with
  --extractor-args "rteradioprogramme:fill_gaps=no"
"""
import base64
import calendar
import datetime as dt
import itertools
import re
import statistics
import urllib.parse

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils import (
    ExtractorError,
    float_or_none,
    int_or_none,
    unescapeHTML,
    unified_strdate,
    unified_timestamp,
    url_or_none,
)
from yt_dlp.utils.traversal import traverse_obj


class _RteBaseIE(InfoExtractor):
    # Geo-restriction handled like yt-dlp's SVT extractor (svt.py): Ireland named in errors,
    # no automatic X-Forwarded-For fake IP (users can still opt in with --xff).
    _GEO_COUNTRIES = ['IE']
    _GEO_BYPASS = False
    _GEO_MSG = 'This video is only available in Ireland'

    # RTE radio stations: URL slug and station id, as in https://www.rte.ie/radio/live_stations/json
    _STATIONS = {
        'radio1': (9, 'RTÉ Radio 1'),
        '2fm': (1, 'RTÉ 2FM'),
        'lyricfm': (16, 'RTÉ Lyric FM'),
        'rnag': (17, 'RTÉ Raidió na Gaeltachta'),
        'gold': (22, 'RTÉ Gold'),
    }

    @classmethod
    def _station_name(cls, slug=None, station_id=None):
        """Station from RTE's own station id first; the URL's station slug is only a fallback."""
        station_id = int_or_none(station_id)
        by_id = next((name for sid, name in cls._STATIONS.values() if sid == station_id), None)
        return by_id or traverse_obj(cls._STATIONS, ((slug or '').lower(), 1))

    @classmethod
    def _station_slug(cls, station_id):
        station_id = int_or_none(station_id)
        return next((slug for slug, (sid, _) in cls._STATIONS.items() if sid == station_id), None)

    @staticmethod
    def _summer_time(year):
        """Irish summer time (UTC+1): EU rule, 01:00 UTC last Sunday of March to last Sunday of October."""
        def last_sunday(month):
            day = max(week[calendar.SUNDAY] for week in calendar.monthcalendar(year, month))
            return calendar.timegm((year, month, day, 1, 0, 0))
        return last_sunday(3), last_sunday(10)

    @classmethod
    def _irish_timestamp(cls, value):
        """Timestamp from an RTE date string. Strings with a UTC marker or offset are taken as such;
        strings without one (or marked IST) are Irish local time."""
        if not value or not isinstance(value, str):
            return None
        value = value.strip()
        if re.search(r'(?:Z|[+-]\d{2}:?\d{2}|\b(?:GMT|UTC|UT))$', value):
            ts = unified_timestamp(value)
        else:
            ts = unified_timestamp(re.sub(r'\s*\bIST$', '', value))  # parsed as if UTC
            if ts is not None:
                start, end = cls._summer_time(dt.datetime.fromtimestamp(ts, dt.timezone.utc).year)
                if start <= ts - 3600 < end:
                    ts -= 3600
        # RTE uses 0001-01-01 and similar as "no date"
        return ts if ts is not None and ts > 0 else None

    @classmethod
    def _irish_local(cls, timestamp):
        """Irish local wall-clock time (naive datetime) for a UTC timestamp."""
        start, end = cls._summer_time(dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).year)
        offset = 3600 if start <= timestamp < end else 0
        return dt.datetime.fromtimestamp(timestamp + offset, dt.timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _utc_date(timestamp):
        """YYYYMMDD in UTC, as yt-dlp derives upload_date from timestamp.

        Playlist entries must carry upload_date themselves: yt-dlp's --dateafter/--datebefore
        check on unopened playlist entries only looks at upload_date, not timestamp."""
        if timestamp is None:
            return None
        return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).strftime('%Y%m%d')

    @staticmethod
    def _normalise_id(item_id):
        item_id = str(item_id).strip().lower()
        return str(int(item_id)) if item_id.isdigit() else item_id

    @staticmethod
    def _clean_title(title):
        title = unescapeHTML(title)
        if not isinstance(title, str):
            return None
        return re.sub(r'\s+', ' ', title.replace('\x00', ' ')).strip() or None

    def _download_json_retry(self, url, video_id, note, query=None, *, end_statuses=(404,)):
        """JSON with yt-dlp's --extractor-retries for transient errors. Returns None for HTTP
        statuses that mean "no more data" (end_statuses); server errors (5xx) are retried, other
        HTTP errors are raised at once."""
        for retry in self.RetryManager():
            try:
                return self._download_json(url, video_id, note, query=query)
            except ExtractorError as e:
                if isinstance(e.cause, HTTPError):
                    if e.cause.status in end_statuses:
                        return None
                    if e.cause.status < 500:
                        raise
                retry.error = e


class _RteVideoBaseIE(_RteBaseIE):
    # Values from the config of RTE's embed player (www.rte.ie/media-embed/dustin/main-*.js):
    # DUSTINP_ENTERTAINMENT_FEED, DUSTIN_PID_LOOKUP, DUSTIN_APP_RTE_SL_ANON_LOGIN_URL,
    # DUSTIN_APP_CONTENT_PROTECTION_ACCOUNT
    _FEED = 'https://feed.entertainment.tv.theplatform.eu/f/1uC-gC/prd-webplayer-programmes/'
    _PID_LOOKUP = 'https://feed.theplatform.eu/f/1uC-gC/rte-prd-media-pid-lookup'
    _ANON_LOGIN = 'https://www.rte.ie/servicelayer/api/anonymouslogin?isocode=IE'
    _MPX_ACCOUNT = 'http://access.auth.theplatform.com/data/Account/2700894001'
    _GUID_PREFIX = 'epic-mpx-manual-'
    _FIELDS = ','.join((
        'guid', 'title', 'plprogram$longDescription', 'plprogram$runtime', 'plprogram$tags',
        'plprogram$programType', 'plprogram$ratings',
        'rte$broadcastDate', 'rte$defaultThumbnail', 'rte$licenceGeos',
        'plprogramavailability$media.plmedia$publicUrl',
        'plprogramavailability$media.media$content.plfile$format',
        'plprogramavailability$media.media$content.plfile$isProtected',
    ))
    _IGNORED_TAGS = ('notSearchable', 'noAds')
    _auth_header = None

    def _query_feed(self, video_id, query, note='Downloading feed'):
        return self._download_json_retry(
            self._FEED, video_id, note, query={'fields': self._FIELDS, **query}, end_statuses=())

    def _get_auth_header(self, video_id):
        if not _RteVideoBaseIE._auth_header:
            token = self._download_json(
                self._ANON_LOGIN, video_id, 'Downloading anonymous token')['mpx_token']
            _RteVideoBaseIE._auth_header = 'Basic ' + base64.b64encode(
                f'{self._MPX_ACCOUNT}:{token}'.encode()).decode()
        return _RteVideoBaseIE._auth_header

    @classmethod
    def _video_id(cls, guid):
        if guid.startswith(cls._GUID_PREFIX):
            return guid[len(cls._GUID_PREFIX):]
        # Guard: a non-rte.ie/video ID that is all digits must never equal a stripped rte.ie/video number
        return f'mpx-{guid}' if guid.isdigit() else guid

    def _entry_metadata(self, entry):
        tags = traverse_obj(entry, ('plprogram$tags', lambda _, v: v['plprogram$title']))
        categories = [t['plprogram$title'] for t in tags if t.get('plprogram$scheme') in ('epic', 'category')]
        other_tags = [t['plprogram$title'] for t in tags if t.get('plprogram$scheme') not in ('epic', 'category', 'subCategory')]
        rating = traverse_obj(entry, ('plprogram$ratings', ..., 'plprogram$rating', {str}, any))
        return {
            'id': self._video_id(entry['guid']),
            'display_id': entry['guid'],
            'title': self._clean_title(entry.get('title')),
            'description': unescapeHTML(entry.get('plprogram$longDescription')) or None,
            'duration': float_or_none(entry.get('plprogram$runtime')) or None,  # 0 = unknown
            'timestamp': int_or_none(entry.get('rte$broadcastDate'), 1000),
            'thumbnail': url_or_none(entry.get('rte$defaultThumbnail')),
            'uploader': 'RTÉ',
            'channel': 'RTÉ',
            'categories': [c for c in categories if c not in self._IGNORED_TAGS] or None,
            'tags': [t for t in other_tags if t not in self._IGNORED_TAGS] or None,
            'media_type': entry.get('plprogram$programType') or 'clip',
            'age_limit': 0 if rating == 'GA' else None,
            'rte_licence': entry.get('rte$licenceGeos'),
        }

    def _extract_entry(self, entry):
        info = self._entry_metadata(entry)
        video_id = info['id']
        media = traverse_obj(entry, ('plprogramavailability$media', 0, {dict})) or {}
        files = media.get('media$content') or []
        # Treat missing protection info as protected
        if not files or any(f.get('plfile$isProtected', True) for f in files):
            self.report_drm(video_id)
        public_url = url_or_none(media.get('plmedia$publicUrl'))
        if not public_url:
            raise ExtractorError('No media URL found', video_id=video_id)

        smil = self._download_webpage(
            public_url, video_id, 'Downloading SMIL',
            headers={'Authorization': self._get_auth_header(video_id)},
            query={'formats': 'm3u', 'format': 'smil'})
        exception = self._search_regex(
            (r'<param[^>]+name="exception"[^>]+value="([^"]+)"', r'exception="([^"]+)"'),
            smil, 'exception', default=None)
        if exception == 'GeoLocationBlocked':
            self.raise_geo_restricted(self._GEO_MSG, countries=self._GEO_COUNTRIES, metadata_available=True)
            return info
        src = url_or_none(unescapeHTML(self._search_regex(
            r'<video[^>]+src="([^"]+)"', smil, 'stream URL', default=None)))
        if not src or '/errorFiles/' in src:
            raise ExtractorError(
                'No playable stream' + (f' ({exception})' if exception else ' (not yet published?)'),
                expected=True)

        formats, subtitles = self._extract_m3u8_formats_and_subtitles(src, video_id, 'mp4', m3u8_id='hls')
        return {**info, 'formats': formats, 'subtitles': subtitles}


class RteVideoIE(_RteVideoBaseIE):
    IE_NAME = 'rte:video'
    IE_DESC = 'RTÉ video clips (rte.ie/video)'
    _VALID_URL = [
        r'https?://(?:www\.)?rte\.ie/video/id/(?P<id>\d+)',
        # dustin embed with a thePlatform ID (contains a non-digit); numeric IDs belong to the older lookup
        r'https?://(?:www\.)?rte\.ie/media-embed/dustin/?\?(?:[^#]*&)?id=(?P<pid>[\w-]*[A-Za-z_-][\w-]*)',
    ]
    _EMBED_REGEX = [r'<iframe[^>]+?\bsrc=["\'](?P<url>(?:https?:)?//(?:www\.)?rte\.ie/media-embed/dustin/?\?[^"\']+)']
    _TESTS = [{
        'url': 'https://www.rte.ie/video/id/38228/',
        'info_dict': {
            'id': '38228',
            'ext': 'mp4',
            'title': 'What traffic restrictions are in place in Dublin from today until Friday?',
        },
    }, {
        'url': 'https://www.rte.ie/media-embed/dustin/?id=vKr_UGcLUBDF',
        'only_matching': True,
    }]

    def _real_extract(self, url):
        mobj = self._match_valid_url(url).groupdict()
        video_id, pid = mobj.get('id'), mobj.get('pid')
        if pid:
            guid = traverse_obj(self._download_json(
                self._PID_LOOKUP, pid, 'Resolving embed ID', query={'bypid': pid}),
                ('entries', 0, 'guid', {str}))
            if not guid:
                raise ExtractorError('Video not found', expected=True)
            video_id = self._video_id(guid)
        else:
            guid = self._GUID_PREFIX + video_id

        entry = traverse_obj(self._query_feed(video_id, {'byGuid': guid}), ('entries', 0, {dict}))
        if not entry:
            raise ExtractorError('Video not found (it may have been removed)', expected=True)
        return self._extract_entry(entry)


class RteVideoCategoryIE(_RteVideoBaseIE):
    IE_NAME = 'rte:video:category'
    IE_DESC = 'RTÉ video category listings (rte.ie/video/category/...)'
    _VALID_URL = r'https?://(?:www\.)?rte\.ie/video(?:/category/(?P<id>[^/?#]+))?/?(?:[?#]|$)'
    _PAGE_SIZE = 100
    _MAX_ITEMS = 1000  # the feed rejects ranges beyond this with HTTP 400
    _TESTS = [{
        'url': 'https://www.rte.ie/video/category/News/',
        'info_dict': {'id': 'news', 'title': 'RTÉ video: News'},
        'playlist_mincount': 10,
    }, {
        'url': 'https://www.rte.ie/video/',
        'only_matching': True,
    }]

    def _entries(self, category):
        seen = set()
        for page in itertools.count():
            start = page * self._PAGE_SIZE + 1
            if start > self._MAX_ITEMS:
                return
            query = {
                'byProgramType': 'clip',
                'sort': 'rte$broadcastDate|desc',
                'range': f'{start}-{start + self._PAGE_SIZE - 1}',
            }
            if category:
                query['byTags'] = f'epic:{category}'
            data = self._query_feed(category or 'all', query, f'Downloading page {page + 1}')
            raw = traverse_obj(data, ('entries', ..., {dict}))
            for entry in raw:
                guid = entry.get('guid')
                # Only items rte.ie/video itself lists, not RTE Player clips sharing the feed
                if not isinstance(guid, str) or not guid.startswith(self._GUID_PREFIX):
                    continue
                info = self._entry_metadata(entry)
                if info['id'] in seen:  # the feed can shift while it is being paged
                    continue
                seen.add(info['id'])
                yield self.url_result(
                    f'https://www.rte.ie/video/id/{info["id"]}/', RteVideoIE, info['id'],
                    upload_date=self._utc_date(info['timestamp']),
                    **{k: v for k, v in info.items() if k not in ('id', 'display_id')})
            # The end is decided by the feed's own page size, before filtering
            if len(raw) < self._PAGE_SIZE:
                return

    def _real_extract(self, url):
        category = self._match_id(url)
        page_url = f'https://www.rte.ie/video/category/{category}/' if category else 'https://www.rte.ie/video/'
        webpage = self._download_webpage(page_url, category or 'all', 'Downloading category page', fatal=False) or ''
        name = category.lower().capitalize() if category else 'all'
        return self.playlist_result(
            self._entries(category), (category or 'all').lower(), f'RTÉ video: {name}',
            thumbnail=self._og_search_thumbnail(webpage, default=None),
            uploader='RTÉ', channel='RTÉ')


class RteClipIE(_RteBaseIE):
    """Everything served by RTE's older lookup (rteavgen/getplaylist): one ID system, one extractor.

    Radio clips and episodes, Bosco embeds (news articles) and numeric dustin embeds (RTE Archives)
    all resolve to the same item ID, so they share one archive key.
    """
    IE_NAME = 'rte:clip'
    IE_DESC = 'RTÉ radio clips and episodes, RTÉ News and RTÉ Archives videos'
    # Numeric item IDs have at least 4 digits; small numbers are live-station ids (live player embeds)
    _ID_RE = r'(?P<id>[\da-fA-F]{8}-[\da-fA-F]{4}-[\da-fA-F]{4}-[\da-fA-F]{4}-[\da-fA-F]{12}|\d{4,})'
    _VALID_URL = [
        rf'https?://(?:www\.)?rte\.ie/radio/(?P<station>[\w-]+)/clips/{_ID_RE}/?(?:[?#]|$)',
        rf'https?://(?:www\.)?rte\.ie/radio/(?P<station>[\w-]+)/[\w-]+/episodes/{_ID_RE}/?(?:[?#]|$)',
        rf'https?://(?:www\.)?rte\.ie/bosco/components/player/iframe\.html\?(?:[^#]*&)?clipid={_ID_RE}(?:[&#]|$)',
        r'https?://(?:www\.)?rte\.ie/media-embed/dustin/?\?(?:[^#]*&)?id=(?P<id>\d{4,})(?:[&#]|$)',
    ]
    _EMBED_REGEX = [
        r'<iframe[^>]+?\b(?:data-)?src=["\'](?P<url>(?:https?:)?//(?:www\.)?rte\.ie/'
        r'(?:bosco/components/player/iframe\.html|media-embed/dustin/?)\?[^"\']+)',
    ]
    _LOOKUP = 'https://www.rte.ie/rteavgen/getplaylist/'
    _TESTS = [{
        'url': 'https://www.rte.ie/radio/radio1/clips/13cf7b4f-b948-44e1-b609-b4ff00a04ccc/',
        'info_dict': {
            'id': '13cf7b4f-b948-44e1-b609-b4ff00a04ccc',
            'ext': 'mp3',
            'title': 'Charities processed 1m bags of unusable clothes last year',
            'series': 'Morning Ireland',
            'channel': 'RTÉ Radio 1',
        },
    }, {
        'url': 'https://www.rte.ie/radio/radio1/clips/10507902/',
        'only_matching': True,
    }, {
        'url': 'https://www.rte.ie/radio/radio1/morning-ireland/episodes/37750aa0-ae57-4857-86e3-b4f900975b80/',
        'only_matching': True,
    }, {
        'url': 'https://www.rte.ie/bosco/components/player/iframe.html?clipid=22640012&autostart=false&app=true',
        'only_matching': True,
    }, {
        'url': 'https://www.rte.ie/media-embed/dustin/?id=11673745',
        'only_matching': True,
    }]

    def _lookup(self, item_id, *, note=None, fatal=True):
        """One item from the older lookup; None (or an error) if RTE doesn't have it."""
        # RTE answers unknown or expired items with HTTP 404 and an explanation in the JSON body
        data = self._download_json(
            self._LOOKUP, item_id, note=note, query={'type': 'web', 'format': 'json', 'id': item_id},
            expected_status=404, fatal=fatal) or {}
        show = traverse_obj(data, ('shows', 0, {dict}))
        if not show and fatal:
            message = unescapeHTML(traverse_obj(data, ('message', {str})))
            raise ExtractorError(
                f'RTÉ said: {message}' if message else 'Item not found', expected=True, video_id=item_id)
        return show

    @staticmethod
    def _medium(show):
        return show.get('medium') or traverse_obj(show, ('media:group', 0, 'medium', {str}))

    def _real_extract(self, url):
        mobj = self._match_valid_url(url).groupdict()
        item_id = self._normalise_id(mobj['id'])
        show = self._lookup(item_id)
        media = traverse_obj(show, ('media:group', 0, {dict})) or {}
        medium = self._medium(show)
        if str(media.get('iscopyprotected')).lower() == 'true':
            self.report_drm(item_id)

        formats = []
        hls_url = url_or_none(f'{media.get("hls_server") or ""}{media.get("hls_url") or ""}')
        if hls_url:
            fmts = self._extract_m3u8_formats(
                hls_url, item_id, 'm4a' if medium == 'audio' else 'mp4', m3u8_id='hls', fatal=False)
            if medium == 'audio':
                for f in fmts:
                    if not f.get('vcodec') or f['vcodec'] == 'unknown':
                        f['vcodec'] = 'none'
            formats.extend(fmts)
        mp3_url = url_or_none(show.get('url'))
        if mp3_url and urllib.parse.urlparse(mp3_url).path.lower().endswith('.mp3'):
            formats.append({
                'url': mp3_url, 'format_id': 'mp3', 'ext': 'mp3',
                'vcodec': 'none', 'acodec': 'mp3',
            })
        if not formats:
            raise ExtractorError('No playable stream found', expected=True, video_id=item_id)

        station = self._station_name(mobj.get('station'), show.get('channelid'))
        uploader = unescapeHTML(show.get('channel'))
        if not uploader or uploader == 'WEB Exclusive':
            uploader = unescapeHTML(show.get('provider')) or 'RTÉ'
        timestamp = self._irish_timestamp(show.get('published'))
        title = self._clean_title(show.get('title'))

        # Episode or clip: the URL says so on radio pages; otherwise RTE's data does. Only items
        # of a radio station count as broadcast episodes (archive audio has no station, and its
        # published date is its upload date, not a broadcast date)
        if '/episodes/' in url:
            media_type = 'episode'
        elif '/clips/' in url:
            media_type = 'clip'
        elif (medium == 'audio' and self._station_slug(show.get('channelid'))
              and (show.get('form') == 'long' or show.get('episode_id') == show.get('itemid'))):
            media_type = 'episode'
        else:
            media_type = 'clip'

        description = unescapeHTML(show.get('description')) or None
        series = self._clean_title(show.get('series'))
        if medium == 'audio':
            # RTE's programme title, for older items whose series field is empty
            series = series or self._clean_title(show.get('ptitle'))
            if not description:
                page_url = url if url.startswith(('http://www.rte.ie/radio/', 'https://www.rte.ie/radio/')) else None
                slug = self._station_slug(show.get('channelid'))
                if not page_url and slug:
                    page_url = f'https://www.rte.ie/radio/{slug}/clips/{item_id}/'
                if page_url:
                    webpage = self._download_webpage(
                        page_url, item_id, 'Downloading radio page for description', fatal=False) or ''
                    description = (self._og_search_description(webpage, default=None)
                                   or self._html_search_meta('description', webpage, default=None))
        genre = unescapeHTML(show.get('genre'))
        return {
            'id': item_id,
            'title': title,
            'description': description,
            'duration': float_or_none(show.get('duration'), 1000),
            'timestamp': timestamp,
            'release_timestamp': timestamp if media_type == 'episode' else None,
            'thumbnail': url_or_none(show.get('thumbnail')),
            'uploader': uploader,
            'channel': station or uploader,
            'series': series,
            'categories': [genre] if genre else None,
            'media_type': media_type,
            'formats': formats,
        }


class RteArchivesIE(_RteBaseIE):
    IE_NAME = 'rte:archives'
    IE_DESC = 'RTÉ Archives pages (rte.ie/archives)'
    _VALID_URL = r'https?://(?:www\.)?rte\.ie/archives/\d{4}/\d{4}/(?P<id>\d+)-[\w-]+'
    _TESTS = [{
        'url': 'https://www.rte.ie/archives/2016/0608/794170-21st-cork-film-festival/',
        'info_dict': {
            'id': '10578396',
            'ext': 'mp4',
            'title': '21st Cork Film Festival',
            'release_date': '19760611',
        },
    }]
    _MONTHS = r'(?:January|February|March|April|May|June|July|August|September|October|November|December)'

    def _release_date(self, webpage):
        """The clip's own broadcast date from the caption.

        Candidates are 'broadcast on <date>' phrases whose year matches the page's year tag.
        Sentences about this episode/report are preferred, sentences about the series (its first
        episode, its run) are avoided; ties go to the later sentence. Without a match, only the
        year tag is used."""
        year = int_or_none(self._search_regex(
            r'<span[^>]+class="date_tag"[^>]*>\s*(\d{4})', webpage, 'year', default=None))
        text = unescapeHTML(re.sub(r'<[^>]+>', ' ', webpage))
        text = re.sub(r'\s+', ' ', text)
        best = None
        for idx, sentence in enumerate(re.split(r'(?<=[.!?])\s+', text)):
            for m in re.finditer(rf'broadcast on (\d{{1,2}})(?:st|nd|rd|th)? ({self._MONTHS}) (\d{{4}})', sentence):
                date = unified_strdate(f'{m.group(1)} {m.group(2)} {m.group(3)}')
                if not date or (year and int(date[:4]) != year):
                    continue
                low = sentence.lower()
                score = 0
                if re.search(r'\bthis (?:episode|programme|report|edition)\b|\breport\b', low):
                    score += 2
                if re.search(r'\bfirst episode\b|\bseries\b|\bran (?:until|for)\b', low):
                    score -= 2
                if best is None or (score, idx) >= best[:2]:
                    best = (score, idx, date)
        if best:
            return {'release_date': best[2]}
        return {'release_year': year} if year else {}

    def _real_extract(self, url):
        page_id = self._match_id(url)
        webpage = self._download_webpage(url, page_id)
        embeds = [*RteClipIE._extract_embed_urls(url, webpage), *RteVideoIE._extract_embed_urls(url, webpage)]
        embeds = list(dict.fromkeys(embeds))
        if not embeds:
            raise ExtractorError('No video found on this page', expected=True)

        keywords = self._html_search_meta('keywords', webpage, default=None)
        page_info = {
            'title': self._og_search_title(webpage, default=None),
            'description': self._og_search_description(webpage, default=None),
            'tags': [k.strip() for k in keywords.split(',') if k.strip()] if keywords else None,
            'uploader': 'RTÉ Archives',
            'channel': 'RTÉ Archives',
            'media_type': 'clip',
        }
        if len(embeds) == 1:
            return self.url_result(
                embeds[0], url_transparent=True, **page_info, **self._release_date(webpage))
        return self.playlist_result(
            [self.url_result(e) for e in embeds], page_id, page_info['title'],
            page_info['description'], uploader='RTÉ Archives', channel='RTÉ Archives')


class RteRadioProgrammeIE(_RteBaseIE):
    IE_NAME = 'rte:radio:programme'
    IE_DESC = 'RTÉ radio programme pages: all episodes (or clips), newest first'
    _VALID_URL = r'https?://(?:www\.)?rte\.ie/radio/(?P<station>[\w-]+)/(?P<id>(?!clips(?:[/?#]|$))[\w-]+)(?:/episodes)?/?(?:[?#]|$)'
    _PAGE_SIZE = 100  # fixed by RTE's episodes/json and clips/json endpoints
    _TESTS = [{
        'url': 'https://www.rte.ie/radio/radio1/morning-ireland/',
        'info_dict': {'id': 'morning-ireland', 'title': 'Morning Ireland'},
        'playlist_mincount': 100,
    }, {
        'url': 'https://www.rte.ie/radio/radio1/morning-ireland/episodes/',
        'only_matching': True,
    }]

    def _list_page(self, station, programme, kind, page):
        """One page of RTE's episodes/json or clips/json; None at the end of the list.

        RTE's num_pages/total_items are unreliable; the list ends with a short page or,
        when the last page is exactly full, with HTTP 404 for the next one."""
        data = self._download_json_retry(
            f'https://www.rte.ie/radio/{station}/{programme}/{kind}/json/', programme,
            f'Downloading {kind} page {page}', query={'page': page},
            # 500 on page 1 = no such list (e.g. doconone); later, only 404 ends the list, so a
            # server error mid-list is retried and then fails instead of silently truncating
            end_statuses=(404, 500) if page == 1 else (404,))
        return traverse_obj(data, ('programmes', lambda _, v: v.get('id') or v.get('itemid'))) or None

    def _item_timestamp(self, item):
        return (self._irish_timestamp(item.get('published'))
                or (lambda t: t if t and t > 0 else None)(int_or_none(item.get('BroadcastDateUnixTime'))))

    def _entry(self, item, station, programme, kind, **extra):
        item_id = self._normalise_id(item.get('id') or item['itemid'])
        if kind == 'clips':
            item_url = f'https://www.rte.ie/radio/{station}/clips/{item_id}/'
        else:
            item_url = f'https://www.rte.ie/radio/{station}/{programme}/episodes/{item_id}/'
        timestamp = self._item_timestamp(item)
        is_episode = kind == 'episodes'
        return self.url_result(
            item_url, RteClipIE, item_id, self._clean_title(item.get('title')),
            timestamp=timestamp, upload_date=self._utc_date(timestamp),
            release_timestamp=timestamp if is_episode else None,
            release_date=self._utc_date(timestamp) if is_episode else None,
            # RTE's list durations are wrong for older (numeric) items; the item itself has the right one
            duration=None if item_id.isdigit() else float_or_none(item.get('duration'), 1000),
            series=self._clean_title(item.get('programme_title')),
            channel=self._station_name(station, item.get('channelid')),
            media_type='episode' if is_episode else 'clip', **extra)

    def _fill_gap(self, station, programme, newer, older, seen):
        # EXPERIMENTAL, off by default (enable with --extractor-args "rteradioprogramme:fill_gaps=yes").
        # Finds episodes that RTE's own episode list skips between two of its pages by searching
        # RTE's older ID sequence for each missing day, and yields them marked rte_gap_filled=True.
        def numeric(items):
            return sorted((i for i in items if str(i.get('id') or i.get('itemid')).isdigit()),
                          key=lambda i: int(i.get('id') or i['itemid']))

        older_num, newer_num = numeric(older), numeric(newer)
        if len(older_num) < 2 or not newer_num:
            return
        lo, hi = older_num[-1], newer_num[0]  # newest older-page item, oldest newer-page item
        lo_id, hi_id = int(lo.get('id') or lo['itemid']), int(hi.get('id') or hi['itemid'])
        lo_ts, hi_ts = self._item_timestamp(lo), self._item_timestamp(hi)
        if not lo_ts or not hi_ts or hi_id <= lo_id or hi_ts <= lo_ts:
            return

        # The programme's rhythm, from the older page: usual gap between episodes, weekdays, time
        times = sorted(filter(None, (self._item_timestamp(i) for i in older_num)))
        spacing = statistics.median(b - a for a, b in zip(times, times[1:])) if len(times) > 1 else 86400
        if hi_ts - lo_ts <= max(2 * 86400, 3 * spacing):
            return  # no gap
        local = [self._irish_local(t) for t in times]
        weekdays = {d.weekday() for d in local}
        hour, minute = statistics.mode((d.hour, d.minute) for d in local)
        title_key = (self._clean_title(lo.get('programme_title') or lo.get('title')) or '').lower()
        channel_id = int_or_none(lo.get('channelid'))
        ids_per_day = (hi_id - lo_id) / ((hi_ts - lo_ts) / 86400)

        first_day = self._irish_local(lo_ts).date() + dt.timedelta(days=1)
        last_day = self._irish_local(hi_ts).date()
        days = [first_day + dt.timedelta(days=n) for n in range((last_day - first_day).days)
                if (first_day + dt.timedelta(days=n)).weekday() in weekdays]
        if not days:
            return
        self.to_screen(
            f'{programme}: RTÉ\'s episode list skips {first_day} to {last_day - dt.timedelta(days=1)}; '
            f'searching for the missing episodes (experimental)')

        clip_ie = self._downloader.get_info_extractor(RteClipIE.ie_key())
        cache = {}

        def probe(item_id):
            if item_id not in cache:
                cache[item_id] = clip_ie._lookup(str(item_id), note=False, fatal=False)
            return cache[item_id]

        def matches(show, day):
            if not show or int_or_none(show.get('channelid')) != channel_id:
                return False
            ts = self._irish_timestamp(show.get('published'))
            if not ts or self._irish_local(ts).date() != day:
                return False
            # RTE's programme name must match exactly (so "News at One Podcast" is not taken
            # for "News at One"); the item title is only used when there is no programme name
            names = {(self._clean_title(show.get(k)) or '').lower() for k in ('ptitle', 'series')} - {''}
            if names:
                return title_key in names
            name = (self._clean_title(show.get('title')) or '').lower()
            return name == title_key or name.startswith(f'{title_key} ')

        budget = 40 * len(days) + 100  # lookups allowed for this gap
        found = []
        anchor_id, anchor_ts = lo_id, lo_ts
        for day in days:
            if budget <= 0:
                self.report_warning(f'{programme}: gap search stopped (lookup limit reached)')
                break
            target_local = dt.datetime.combine(day, dt.time(hour, minute))
            start, end = self._summer_time(day.year)
            target_ts = calendar.timegm(target_local.timetuple())
            target_ts -= 3600 if start <= target_ts - 3600 < end else 0
            guess = anchor_id + round(ids_per_day * (target_ts - anchor_ts) / 86400)
            # Re-centre on the right day once, then search outwards around the guess
            show = probe(guess)
            budget -= 1
            if show:
                ts = self._irish_timestamp(show.get('published'))
                if ts and abs(ts - target_ts) > 43200:
                    guess += round(ids_per_day * (target_ts - ts) / 86400)
            hit = None
            for offset in itertools.chain([0], *zip(range(1, 61), range(-1, -61, -1))):
                if budget <= 0:
                    break
                cand = guess + offset
                if not lo_id < cand < hi_id:
                    continue
                show = probe(cand)
                budget -= 1
                if matches(show, day):
                    hit = cand, show
                    break
            if not hit:
                continue
            cand, show = hit
            ts = self._irish_timestamp(show.get('published'))
            anchor_id, anchor_ts = cand, ts or target_ts
            if str(cand) in seen:
                continue
            seen.add(str(cand))
            found.append(self.url_result(
                f'https://www.rte.ie/radio/{station}/{programme}/episodes/{cand}/', RteClipIE, str(cand),
                self._clean_title(show.get('title')),
                timestamp=ts, upload_date=self._utc_date(ts),
                release_timestamp=ts, release_date=self._utc_date(ts),
                duration=float_or_none(show.get('duration'), 1000),
                series=self._clean_title(show.get('ptitle') or show.get('series')),
                channel=self._station_name(station, show.get('channelid')),
                media_type='episode', rte_gap_filled=True))
        self.to_screen(f'{programme}: found {len(found)} of {len(days)} possible missing episodes')
        yield from reversed(found)  # newest first, like the rest of the list

    def _entries(self, station, programme, kind, first_page):
        fill_gaps = (kind == 'episodes'
                     and self._configuration_arg('fill_gaps', ['no'])[0] in ('yes', 'true', '1', 'on'))
        seen, previous = set(), None
        for page in itertools.count(1):
            items = first_page if page == 1 else self._list_page(station, programme, kind, page)
            if not items:
                return
            if fill_gaps and previous:
                yield from self._fill_gap(station, programme, previous, items, seen)
            for item in items:
                entry = self._entry(item, station, programme, kind)
                if entry['id'] in seen:
                    continue
                seen.add(entry['id'])
                yield entry
            if len(items) < self._PAGE_SIZE:
                return
            previous = items

    def _page_link_entries(self, webpage, station, programme):
        """Last resort: episode and clip links on the programme page itself (no dates)."""
        seen = set()
        for path in re.findall(
                rf'href="(?:https?://www\.rte\.ie)?(/radio/[\w-]+/(?:{re.escape(programme)}/episodes|clips)/[\da-fA-F-]{{4,}}/)"',
                webpage):
            item_url = f'https://www.rte.ie{path}'
            mobj = RteClipIE._match_valid_url(item_url)
            if not mobj or path in seen:
                continue
            seen.add(path)
            yield self.url_result(item_url, RteClipIE, self._normalise_id(mobj.group('id')),
                                  media_type='episode' if '/episodes/' in path else 'clip')

    def _real_extract(self, url):
        station, programme = (s.lower() for s in self._match_valid_url(url).group('station', 'id'))
        webpage = self._download_webpage(
            f'https://www.rte.ie/radio/{station}/{programme}/', programme, fatal=False) or ''
        json_ld = list(self._yield_json_ld(webpage, programme, fatal=False))
        show = traverse_obj(json_ld, (lambda _, v: v.get('@type') == 'Show', any)) or {}

        # Episodes; for clip-only programmes, clips; failing both, the page's own links
        kind, first = 'episodes', self._list_page(station, programme, 'episodes', 1)
        if not first:
            kind, first = 'clips', self._list_page(station, programme, 'clips', 1)
        if first:
            entries = self._entries(station, programme, kind, first)
        else:
            entries = list(self._page_link_entries(webpage, station, programme))
            if not entries:
                raise ExtractorError('Not an RTÉ radio programme page (no episodes found)', expected=True)

        title = (self._clean_title(show.get('name'))
                 or self._html_search_meta('twitter:title', webpage, default=None)
                 or self._clean_title(traverse_obj(first, (0, 'programme_title'))) or programme)
        channel = self._station_name(station, traverse_obj(first, (0, 'channelid')))
        return self.playlist_result(
            entries, programme, title,
            unescapeHTML(show.get('description')) or self._og_search_description(webpage, default=None),
            thumbnail=url_or_none(show.get('image')), uploader=channel, channel=channel)
