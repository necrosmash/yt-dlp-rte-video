"""
yt-dlp plugin: RTE video clips (rte.ie/video) and category listings.

Install: <plugins dir>/rte-video/yt_dlp_plugins/extractor/rte_video.py
  e.g. ~/.config/yt-dlp/plugins/rte-video/yt_dlp_plugins/extractor/rte_video.py

Supported URLs
  https://www.rte.ie/video/id/38228/                     single clip
  https://www.rte.ie/media-embed/dustin/?id=vKr_UGcLUBDF  embedded clip
  https://www.rte.ie/video/category/News/                category playlist
  https://www.rte.ie/video/                              everything on rte.ie/video

Items the feed marks as DRM-protected are reported as DRM and not downloaded.
RTE Player (/player/...) episodes are DRM-protected and are not handled here.
"""
import base64

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import (
    ExtractorError,
    OnDemandPagedList,
    float_or_none,
    int_or_none,
    unescapeHTML,
    url_or_none,
)
from yt_dlp.utils.traversal import traverse_obj


class _RteVideoBaseIE(InfoExtractor):
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
        'rte$broadcastDate', 'rte$defaultThumbnail',
        'plprogramavailability$media.plmedia$publicUrl',
        'plprogramavailability$media.media$content.plfile$format',
        'plprogramavailability$media.media$content.plfile$isProtected',
    ))
    _auth_header = None

    def _query_feed(self, video_id, query, note='Downloading feed'):
        return self._download_json(self._FEED, video_id, note, query={
            'fields': self._FIELDS, **query})

    def _get_auth_header(self, video_id):
        if not _RteVideoBaseIE._auth_header:
            token = self._download_json(
                self._ANON_LOGIN, video_id, 'Downloading anonymous token')['mpx_token']
            _RteVideoBaseIE._auth_header = 'Basic ' + base64.b64encode(
                f'{self._MPX_ACCOUNT}:{token}'.encode()).decode()
        return _RteVideoBaseIE._auth_header

    def _video_id(self, guid):
        return guid[len(self._GUID_PREFIX):] if guid.startswith(self._GUID_PREFIX) else guid

    def _entry_metadata(self, entry):
        tags = traverse_obj(entry, ('plprogram$tags', ..., 'plprogram$title', {str}))
        return {
            'id': self._video_id(entry['guid']),
            'display_id': entry['guid'],
            'title': unescapeHTML(entry.get('title')),
            'description': unescapeHTML(entry.get('plprogram$longDescription')) or None,
            'duration': float_or_none(entry.get('plprogram$runtime')),
            'timestamp': int_or_none(entry.get('rte$broadcastDate'), 1000),
            'thumbnail': url_or_none(entry.get('rte$defaultThumbnail')),
            'tags': [t for t in tags if t not in ('notSearchable', 'noAds')],
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
        src = url_or_none(unescapeHTML(self._search_regex(
            r'<video[^>]+src="([^"]+)"', smil, 'stream URL', default=None)))
        if not src or '/errorFiles/' in src:
            exception = self._search_regex(r'exception="([^"]+)"', smil, 'exception', default=None)
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
        r'https?://(?:www\.)?rte\.ie/media-embed/dustin/?\?(?:[^#]*&)?id=(?P<pid>[\w-]+)',
    ]
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
        'info_dict': {'id': 'News', 'title': 'RTÉ video: News'},
        'playlist_mincount': 10,
    }, {
        'url': 'https://www.rte.ie/video/',
        'only_matching': True,
    }]

    def _fetch_page(self, category, page):
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
        for entry in traverse_obj(data, ('entries', lambda _, v: v['guid'])):
            # Only items rte.ie/video itself lists, not RTE Player clips sharing the tag
            if not entry['guid'].startswith(self._GUID_PREFIX):
                continue
            info = self._entry_metadata(entry)
            yield self.url_result(
                f'https://www.rte.ie/video/id/{info["id"]}/', RteVideoIE, info['id'],
                **{k: v for k, v in info.items() if k not in ('id', 'display_id')})

    def _real_extract(self, url):
        category = self._match_id(url)
        return self.playlist_result(
            OnDemandPagedList(lambda p: self._fetch_page(category, p), self._PAGE_SIZE),
            category or 'all', f'RTÉ video: {category or "all"}')
