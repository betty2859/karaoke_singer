# -*- coding: utf-8 -*-
"""노래방 도우미 플러그인 (TJ / 금영 번호 검색 + YouTube MR)."""
from __future__ import annotations

import http.cookiejar
import html
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from plugins.metadata.base import BaseMetadataProvider

from .persistent import (
    BRAND_KY,
    BRAND_TJ,
    export_seed,
    known_numbers,
    load_songs,
    normalize_brand,
    present_keys,
)
from .sync_songs import sync_brand
from . import queue_store
from . import mr_resolver

# ── TJ미디어 공식 TOP100/HOT100 차트 ──────────────────────────────────────
# 예전에는 tj_chart.py라는 별도 파일로 분리해뒀는데, BookOasis의 zip "업데이트"가
# 기존 파일만 덮어쓰고 새로 추가된 파일은 반영하지 못하는 경우가 있어서(v1.1.0/v2.1.0
# 에서 실제로 tj_chart.py/ky_chart.py가 안 들어가는 문제가 있었음), 이 파일
# (karaoke_singer.py) 안으로 합쳐서 "새 파일 추가"가 필요 없도록 했다.
TJ_CHART_BASE = 'https://www.tjmedia.com'
TJ_CHART_PAGE = TJ_CHART_BASE + '/chart/top100'
TJ_CHART_API = TJ_CHART_BASE + '/legacy/api/topAndHot100'
_TJ_CHART_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
_TJ_CHART_TIMEOUT = 15
_TJ_CHART_CACHE = {}
_TJ_CHART_CACHE_TTL = 6 * 3600


def _tj_cache_key(chart_type, str_type, start_date, end_date):
    return '%s|%s|%s|%s' % (chart_type, str_type, start_date, end_date)


def _tj_fetch_session():
    """차트 페이지를 GET해서 세션 쿠키(JSESSIONID, CSRF_TOKEN)를 확보한다."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    req = urllib.request.Request(TJ_CHART_PAGE, headers={'User-Agent': _TJ_CHART_UA})
    with opener.open(req, timeout=_TJ_CHART_TIMEOUT) as resp:
        resp.read()
    return jar, opener


def _tj_csrf_from_jar(jar):
    for cookie in jar:
        if cookie.name == 'CSRF_TOKEN':
            return cookie.value
    return ''


def _tj_parse_items(payload):
    raw_items = ((payload.get('resultData') or {}).get('items')) or []
    items = []
    for row in raw_items:
        no = str(row.get('pro') or '').strip()
        title = str(row.get('indexTitle') or '').strip()
        if not no or not title:
            continue
        try:
            rank = int(row.get('rank') or 0)
        except (TypeError, ValueError):
            rank = 0
        items.append({
            'rank': rank,
            'no': no,
            'title': title,
            'singer': str(row.get('indexSong') or '').strip(),
            'lyricist': str(row.get('word') or '').strip(),
            'composer': str(row.get('com') or '').strip(),
            'has_mv': row.get('mv_yn') == 'Y',
            'thumb': row.get('imgthumb_path') or '',
        })
    items.sort(key=lambda x: x['rank'] or 999999)
    return items


def _tj_fetch_chart(chart_type='TOP', str_type='', start_date=None, end_date=None, force=False):
    """TOP100(chart_type='TOP') 또는 HOT100(chart_type='HOT')을 가져온다.

    반환값: (items 또는 None, 에러 메시지 또는 None)
    """
    chart_type = 'HOT' if str(chart_type or '').upper() == 'HOT' else 'TOP'
    end_date = end_date or time.strftime('%Y-%m-%d')
    start_date = start_date or time.strftime('%Y-%m-%d', time.localtime(time.time() - 29 * 86400))
    key = _tj_cache_key(chart_type, str_type, start_date, end_date)

    if not force:
        cached = _TJ_CHART_CACHE.get(key)
        if cached and (time.time() - cached['at']) < _TJ_CHART_CACHE_TTL:
            return cached['items'], None

    try:
        jar, opener = _tj_fetch_session()
        token = _tj_csrf_from_jar(jar)
        body = urllib.parse.urlencode({
            'chartType': chart_type,
            'searchStartDate': start_date,
            'searchEndDate': end_date,
            'strType': str_type or '',
        }).encode('utf-8')
        req = urllib.request.Request(TJ_CHART_API, data=body, headers={
            'User-Agent': _TJ_CHART_UA,
            'Accept': '*/*',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Origin': TJ_CHART_BASE,
            'Referer': TJ_CHART_PAGE,
            'X-Requested-With': 'XMLHttpRequest',
            'X-CSRF-TOKEN': token,
        })
        with opener.open(req, timeout=_TJ_CHART_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode('utf-8', errors='replace'))
    except urllib.error.HTTPError as exc:
        return None, 'TJ 차트 서버 HTTP %s' % exc.code
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError) as exc:
        return None, str(exc)

    items = _tj_parse_items(payload)
    if items:
        _TJ_CHART_CACHE[key] = {'at': time.time(), 'items': items}
        return items, None
    return None, str(payload.get('resultMsg') or '차트 데이터를 가져오지 못했습니다.')


# ── 금영(KYSing) 공식 인기차트 ────────────────────────────────────────────
KY_CHART_BASE = 'https://kysing.kr'
KY_CHART_PAGE = KY_CHART_BASE + '/popular/'
_KY_CHART_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
_KY_CHART_TIMEOUT = 15
_KY_CHART_CACHE = {}
_KY_CHART_CACHE_TTL = 6 * 3600
_KY_CHART_PERIODS = ('', 'w', 'm', 'y')

_KY_BLOCK_RE = re.compile(r'<ul class="popular_chart_list clear">(.*?)</ul>', re.DOTALL)
_KY_NUM_RE = re.compile(r'<li class="popular_chart_num">(\d+)</li>')
_KY_RANK_RE = re.compile(r'class="popular_chart_link">(\d+)<')
_KY_TITLE_RE = re.compile(r'<span title="([^"]*)" class="tit">')
_KY_SINGER_RE = re.compile(r'<li class="popular_chart_sng" title="([^"]*)"')
_KY_YT_RE = re.compile(r'class="popular_chart_ytb"><a href="([^"]+)"')


def _ky_cache_key(period, rng):
    return '%s|%s' % (period, rng)


def _ky_parse_html(text):
    items = []
    for block in _KY_BLOCK_RE.findall(text):
        num_m = _KY_NUM_RE.search(block)
        if not num_m:
            continue  # 컬럼 헤더 줄(곡번호가 없음)은 건너뛴다.
        no = num_m.group(1)
        rank_m = _KY_RANK_RE.search(block)
        try:
            rank = int(rank_m.group(1)) if rank_m else 0
        except ValueError:
            rank = 0
        title_m = _KY_TITLE_RE.search(block)
        title = html.unescape(title_m.group(1)).strip() if title_m else ''
        singer_m = _KY_SINGER_RE.search(block)
        singer = html.unescape(singer_m.group(1)).strip() if singer_m else ''
        yt_m = _KY_YT_RE.search(block)
        youtube = yt_m.group(1) if yt_m else ''
        if not no or not title:
            continue
        items.append({'rank': rank, 'no': no, 'title': title, 'singer': singer, 'youtube': youtube})
    items.sort(key=lambda x: x['rank'] or 999999)
    return items


def _ky_fetch_chart(period='', rng=1, force=False):
    """금영 인기차트를 가져온다.

    period: ''(일간, 기본값), 'w'(주간), 'm'(월간), 'y'(연간)
    rng: 1(1~50위, 기본값) 또는 2(51~100위)
    반환값: (items 또는 None, 에러 메시지 또는 None)
    """
    period = period if period in _KY_CHART_PERIODS else ''
    rng = 2 if str(rng) == '2' else 1
    key = _ky_cache_key(period, rng)

    if not force:
        cached = _KY_CHART_CACHE.get(key)
        if cached and (time.time() - cached['at']) < _KY_CHART_CACHE_TTL:
            return cached['items'], None

    url = '%s?period=%s&range=%s' % (KY_CHART_PAGE, period, rng)
    try:
        req = urllib.request.Request(url, headers={'User-Agent': _KY_CHART_UA, 'Accept': 'text/html'})
        with urllib.request.urlopen(req, timeout=_KY_CHART_TIMEOUT) as resp:
            text = resp.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as exc:
        return None, 'KY 차트 서버 HTTP %s' % exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, str(exc)

    items = _ky_parse_html(text)
    if items:
        _KY_CHART_CACHE[key] = {'at': time.time(), 'items': items}
        return items, None
    return None, '금영 차트 데이터를 가져오지 못했습니다.'

YOUTUBE_SEARCH = 'https://www.googleapis.com/youtube/v3/search'
REQUEST_TIMEOUT = 12
_SYNC_LOCK = threading.RLock()
_SYNC_STATE = {
    'running': False,
    'brand': '',
    'message': '',
    'added': 0,
    'skipped': 0,
    'error': '',
    'stop': threading.Event(),
    'thread': None,
}


def _apply_sync_state(**fields):
    with _SYNC_LOCK:
        _SYNC_STATE.update(fields)


def _execute_sync(brand, db_type='general', force_full=False):
    """실제 동기화 실행 로직. 수동(버튼) 동기화와 자동(백그라운드 매일 1회) 동기화가 공유한다."""
    want = normalize_brand(brand)
    existing = len(known_numbers(load_songs(db_type).get('songs') or [], want))
    stop_event = _SYNC_STATE['stop']

    def progress_cb(info):
        _apply_sync_state(
            message=info.get('message') or '',
            added=int(info.get('added') or 0),
            skipped=int(info.get('skipped') or 0),
        )

    try:
        result = sync_brand(
            want,
            existing_count=existing,
            db_type=db_type,
            stop_event=stop_event,
            progress_cb=progress_cb,
            force_full=force_full,
        )
        stamp = time.strftime('%Y-%m-%d %H:%M:%S')
        from .persistent import upsert_songs
        upsert_songs(want, [], updated_at=stamp)
        if result.get('stopped'):
            message = '동기화를 중지했습니다. 추가 %s곡, 건너뜀 %s곡' % (
                result.get('added') or 0, result.get('skipped') or 0,
            )
        elif result.get('early_stop'):
            message = '신규 곡만 반영했습니다. 추가 %s곡, 기존 번호 건너뜀 %s곡' % (
                result.get('added') or 0, result.get('skipped') or 0,
            )
        else:
            message = '동기화 완료. 추가 %s곡, 건너뜀 %s곡' % (
                result.get('added') or 0, result.get('skipped') or 0,
            )
        _apply_sync_state(
            running=False,
            message=message,
            added=int(result.get('added') or 0),
            skipped=int(result.get('skipped') or 0),
            error='',
        )
    except Exception as exc:
        _apply_sync_state(running=False, error=str(exc), message='동기화 실패')


# --- 매일 1회 자동 동기화 -----------------------------------------------------
# DB(TJ/금영 곡 목록)가 외부에서 계속 바뀌기 때문에, 매 요청마다 실시간으로 상대
# 사이트를 조회하는 대신(속도 저하·상대 사이트 장애에 취약) 하루 한 번 새벽에
# 백그라운드로 자동 동기화한다. 평소 조회/검색은 여전히 빠른 로컬 캐시를 쓴다.
AUTO_SYNC_HOUR = 4  # 새벽 4시(서버 로컬 시간) 이후 그날의 첫 체크에서 실행
AUTO_SYNC_CHECK_INTERVAL = 600  # 10분마다 조건을 확인
_AUTO_SYNC_LOCK = threading.Lock()
_AUTO_SYNC_STARTED = False


def _auto_sync_brand(brand, db_type='general'):
    want = normalize_brand(brand)
    with _SYNC_LOCK:
        if _SYNC_STATE.get('running'):
            return False
        _SYNC_STATE['stop'] = threading.Event()
        _SYNC_STATE.update({
            'running': True,
            'brand': want,
            'message': '자동 동기화를 시작합니다… (매일 새벽 자동 실행)',
            'added': 0,
            'skipped': 0,
            'error': '',
        })
    _execute_sync(want, db_type=db_type, force_full=False)
    return True


def _auto_sync_tick():
    from .persistent import get_auto_sync_date, set_auto_sync_date
    now = time.localtime()
    if now.tm_hour < AUTO_SYNC_HOUR:
        return
    today = time.strftime('%Y-%m-%d', now)
    if get_auto_sync_date() == today:
        return
    with _SYNC_LOCK:
        if _SYNC_STATE.get('running'):
            return  # 수동 동기화가 진행 중이면 다음 체크 때 다시 시도
    # 먼저 오늘 날짜를 기록해둔다 - 동기화 자체가 실패하더라도 같은 날 계속 재시도하며
    # 상대 사이트에 부담을 주지 않고, 재시도는 다음날로 미룬다.
    set_auto_sync_date(today)
    for brand in (BRAND_TJ, BRAND_KY):
        _auto_sync_brand(brand)


def _auto_sync_loop():
    # 기동 직후 바로 체크하지 않고 살짝 지연을 둔다.
    time.sleep(30)
    while True:
        try:
            _auto_sync_tick()
        except Exception:
            pass
        time.sleep(AUTO_SYNC_CHECK_INTERVAL)


def _start_auto_sync():
    global _AUTO_SYNC_STARTED
    with _AUTO_SYNC_LOCK:
        if _AUTO_SYNC_STARTED:
            return
        _AUTO_SYNC_STARTED = True
        threading.Thread(target=_auto_sync_loop, daemon=True).start()


_start_auto_sync()


class KaraokeSingerMetadataProvider(BaseMetadataProvider):
    id = "karaoke_singer"
    name = "노래방 도우미"
    is_searchable = False
    enabled = True

    config_schema = [
        {
            "key": "YOUTUBE_API_KEY",
            "label": "YouTube Data API 키 (MR 검색)",
            "type": "password",
            "default": "",
            "description": "1차는 MR 캐시/로컬 파일, 2차 yt-dlp, 3차 YouTube Data API 순서로 사용합니다.",
        },
        {
            "key": "YTDLP_PATH",
            "label": "yt-dlp 경로",
            "type": "text",
            "default": "yt-dlp",
            "description": "컨테이너의 yt-dlp 실행 파일 경로입니다. PATH에 있으면 yt-dlp로 충분합니다. 플러그인 bin/yt-dlp도 자동 확인합니다.",
        },
        {
            "key": "MR_LOCAL_PATHS",
            "label": "로컬 MR 경로",
            "type": "text",
            "default": "",
            "description": "MP3/MP4 등 MR이 있는 경로를 ; 로 구분합니다. 예: /media/karaoke;/music/mr",
        },
        {
            "key": "MR_LOCAL_PUBLIC_MAPS",
            "label": "로컬 MR 웹 경로 매핑",
            "type": "text",
            "default": "",
            "description": "브라우저에서 접근 가능한 URL로 매핑할 때 사용합니다. 예: /media/karaoke=/media/karaoke",
        },
        {
            "key": "MR_YTDLP_RESULTS",
            "label": "yt-dlp 검색 후보 수",
            "type": "number",
            "default": 8,
            "description": "검색어당 가져올 YouTube 후보 수입니다.",
        },
        {
            "key": "MR_SEARCH_TIMEOUT",
            "label": "MR 검색 제한시간(초)",
            "type": "number",
            "default": 12,
            "description": "yt-dlp 한 번의 검색 제한시간입니다.",
        },
    ]

    category_tab = {
        "title": "노래방",
        "icon": "fa-solid fa-microphone-lines",
        "order": 86,
        "sessions": "all",
    }

    update_manifest = {
        "enabled": True,
        "provider": "github-raw",
        "raw_base_url": (
            "https://raw.githubusercontent.com/tjeodjq/bookoasis/main/plugins/metadata/karaoke_singer"
        ),
        "files": [
            "karaoke_singer.py",
            "mr_resolver.py",
            "persistent.py",
            "queue_store.py",
            "sync_songs.py",
            "__init__.py",
            "index.html",
            "script.js",
            "style.css",
            "VERSION",
            "README.md",
            "install_ytdlp.sh",
        ],
        "version_file": "VERSION",
        "version_key": "plugin version",
        "show_update_button": True,
    }

    def search(self, db_type, query):
        return []

    def apply(self, db_type, book_id, item_data):
        return False, "노래방 도우미는 도서 메타데이터 매칭을 지원하지 않습니다."

    def _arg(self, key, default=''):
        try:
            from flask import request
            val = request.args.get(key)
            return val if val not in (None, '') else default
        except Exception:
            return default

    def _cfg(self, db_type):
        cfg = self.get_plugin_config(db_type, default={}) or {}
        return {
            'youtube_api_key': str(cfg.get('YOUTUBE_API_KEY') or '').strip(),
            'YOUTUBE_API_KEY': str(cfg.get('YOUTUBE_API_KEY') or '').strip(),
            'YTDLP_PATH': str(cfg.get('YTDLP_PATH') or 'yt-dlp').strip(),
            'MR_LOCAL_PATHS': str(cfg.get('MR_LOCAL_PATHS') or '').strip(),
            'MR_LOCAL_PUBLIC_MAPS': str(cfg.get('MR_LOCAL_PUBLIC_MAPS') or '').strip(),
            'MR_YTDLP_RESULTS': cfg.get('MR_YTDLP_RESULTS') or 8,
            'MR_SEARCH_TIMEOUT': cfg.get('MR_SEARCH_TIMEOUT') or 45,
        }

    def _sync_snapshot(self):
        with _SYNC_LOCK:
            return {
                'running': bool(_SYNC_STATE.get('running')),
                'brand': _SYNC_STATE.get('brand') or '',
                'message': _SYNC_STATE.get('message') or '',
                'added': int(_SYNC_STATE.get('added') or 0),
                'skipped': int(_SYNC_STATE.get('skipped') or 0),
                'error': _SYNC_STATE.get('error') or '',
            }

    def _set_sync(self, **fields):
        with _SYNC_LOCK:
            _SYNC_STATE.update(fields)

    def _filter_songs(self, songs, brand, category, letter, query, favorite_ids=None):
        brand = normalize_brand(brand) if brand and brand != 'all' else ''
        query = str(query or '').strip().lower()
        letter = str(letter or '').strip()
        category = str(category or '').strip()
        out = []
        for item in songs:
            # songs는 load_songs()에서 이미 annotate_song()이 적용되고 제목 기준으로
            # 정렬된 상태로 넘어온다. 여기서 다시 annotate_song()을 호출하는 건(예전
            # 코드) 11만곡 이상에서 매 요청마다 중복 비용이 커서 제거했다 - 순서도
            # 이미 정렬된 상태 그대로 유지된다.
            if favorite_ids is not None:
                song_key = '%s:%s' % (item.get('brand') or '', item.get('no') or '')
                if song_key not in favorite_ids:
                    continue
            if brand and item.get('brand') != brand:
                continue
            if category and category != 'all' and item.get('title_first_category') != category:
                continue
            if letter and letter != 'all' and item.get('title_first_key') != letter:
                continue
            if query:
                blob = item.get('_search_blob') or ''
                if query not in blob:
                    continue
            out.append(item)
        return out

    def _list_payload(self, db_type):
        payload = load_songs(db_type)
        songs = payload.get('songs') or []
        brand = self._arg('brand', 'all')
        category = self._arg('category', 'all')
        letter = self._arg('letter', 'all')
        query = self._arg('q', '')
        favorites_only = self._arg('favorites_only', '0') == '1'
        favorite_ids = None
        if favorites_only:
            raw_fav = str(self._arg('favorites', '') or '')
            favorite_ids = {x.strip() for x in raw_fav.split(',') if x.strip()}
        try:
            offset = max(0, int(self._arg('offset', '0') or 0))
        except (TypeError, ValueError):
            offset = 0
        try:
            limit = min(200, max(1, int(self._arg('limit', '80') or 80)))
        except (TypeError, ValueError):
            limit = 80
        filtered = self._filter_songs(songs, brand, category, letter, query, favorite_ids=favorite_ids)
        page = [{k: v for k, v in item.items() if k != '_search_blob'} for item in filtered[offset:offset + limit]]
        tj_count = len(known_numbers(songs, BRAND_TJ))
        ky_count = len(known_numbers(songs, BRAND_KY))
        return {
            'success': True,
            'view': 'list',
            'songs': page,
            'total': len(filtered),
            'offset': offset,
            'limit': limit,
            'page': (offset // limit) + 1 if limit else 1,
            'pages': ((len(filtered) + limit - 1) // limit) if limit else 1,
            'counts': {
                'all': len(songs),
                'tj': tj_count,
                'kumyoung': ky_count,
            },
            'keys': present_keys(songs),
            'updated': payload.get('updated') or {},
            'sync': self._sync_snapshot(),
            'youtube_configured': bool(self._cfg(db_type).get('youtube_api_key')),
        }

    def _run_sync(self, brand, db_type='general', force_full=False):
        _execute_sync(brand, db_type=db_type, force_full=force_full)

    def _start_sync(self, brand, db_type='general', force_full=False):
        want = normalize_brand(brand)
        if want not in (BRAND_TJ, BRAND_KY):
            return {'success': False, 'error': 'brand 는 tj 또는 kumyoung 이어야 합니다.'}
        with _SYNC_LOCK:
            if _SYNC_STATE.get('running'):
                return {
                    'success': False,
                    'error': '이미 동기화가 진행 중입니다.',
                    'sync': self._sync_snapshot(),
                }
            _SYNC_STATE['stop'] = threading.Event()
            _SYNC_STATE.update({
                'running': True,
                'brand': want,
                'message': '전체 동기화를 시작합니다… (조기 종료 없이 모든 번호대를 훑습니다)' if force_full else '동기화를 시작합니다…',
                'added': 0,
                'skipped': 0,
                'error': '',
            })
            worker = threading.Thread(target=self._run_sync, args=(want, db_type, force_full), daemon=True)
            _SYNC_STATE['thread'] = worker
            worker.start()
        return {'success': True, 'view': 'sync_start', 'sync': self._sync_snapshot()}

    def _stop_sync(self):
        with _SYNC_LOCK:
            event = _SYNC_STATE.get('stop')
            if event:
                event.set()
            if _SYNC_STATE.get('running'):
                _SYNC_STATE['message'] = '중지 요청을 보냈습니다…'
        return {'success': True, 'view': 'sync_stop', 'sync': self._sync_snapshot()}

    def _youtube_search(self, db_type):
        """하위 호환용 YouTube 검색. 실제 재생은 mr_resolver를 사용한다."""
        title = self._arg('title', '')
        singer = self._arg('singer', '')
        song = {'id': self._arg('id', ''), 'title': title, 'singer': singer, 'brand': self._arg('brand', ''), 'no': self._arg('no', '')}
        result, trace = mr_resolver.resolve(song, self._cfg(db_type), force=self._arg('force', '0') == '1')
        return {'success': result.get('status') == 'ok', 'view': 'mr_resolve', 'result': result, 'trace': trace, 'diagnostics': mr_resolver.diagnostics(self._cfg(db_type))}

    def _mr_resolve(self, db_type):
        raw = self._arg('song', '')
        try:
            song = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {'success': False, 'view': 'mr_resolve', 'error': 'song 데이터가 올바른 JSON이 아닙니다.'}
        if not isinstance(song, dict):
            return {'success': False, 'view': 'mr_resolve', 'error': 'song 데이터가 올바르지 않습니다.'}
        try:
            result, trace = mr_resolver.resolve(song, self._cfg(db_type), force=self._arg('force', '0') == '1')
            return {
                'success': result.get('status') == 'ok',
                'view': 'mr_resolve',
                'result': result,
                'trace': trace,
                'diagnostics': mr_resolver.diagnostics(self._cfg(db_type)),
            }
        except Exception as exc:
            return {'success': False, 'view': 'mr_resolve', 'error': str(exc), 'diagnostics': mr_resolver.diagnostics(self._cfg(db_type))}

    def _mr_cache_clear(self):
        key = self._arg('song_key', '')
        try:
            removed = queue_store.clear_mr_cache(key or None)
            return {'success': True, 'view': 'mr_cache_clear', 'removed': removed}
        except Exception as exc:
            return {'success': False, 'view': 'mr_cache_clear', 'error': str(exc)}

    def _export_seed(self, db_type):
        # 현재 곡 목록을 플러그인 폴더의 songs_seed.json으로 내보낸다. 이 파일과 함께
        # 플러그인 폴더를 재배포하면, 새로 설치하는 사용자는 동기화 없이 바로 전체
        # 목록을 쓸 수 있다(앱이 처음 켜질 때 곡 데이터가 비어 있으면 자동으로 가져옴).
        try:
            info = export_seed(db_type)
            return {'success': True, 'view': 'export_seed', **info}
        except Exception as exc:
            return {'success': False, 'view': 'export_seed', 'error': str(exc)}

    def _mr_report_error(self, db_type):
        raw = self._arg('song', '')
        video_id = self._arg('video_id', '')
        reason = self._arg('reason', 'embed_disabled')
        try:
            song = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {'success': False, 'view': 'mr_report_error', 'error': 'song 데이터가 올바른 JSON이 아닙니다.'}
        if not isinstance(song, dict):
            return {'success': False, 'view': 'mr_report_error', 'error': 'song 데이터가 올바르지 않습니다.'}
        try:
            result, trace = mr_resolver.report_playback_error(song, video_id, self._cfg(db_type), reason=reason)
            return {
                'success': result.get('status') == 'ok',
                'view': 'mr_report_error',
                'result': result,
                'trace': trace,
            }
        except Exception as exc:
            return {'success': False, 'view': 'mr_report_error', 'error': str(exc)}

    def _mr_set_manual(self):
        raw = self._arg('song', '')
        url = self._arg('url', '')
        try:
            song = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {'success': False, 'view': 'mr_set_manual', 'error': 'song 데이터가 올바른 JSON이 아닙니다.'}
        if not isinstance(song, dict) or not song.get('title'):
            return {'success': False, 'view': 'mr_set_manual', 'error': 'song 데이터가 올바르지 않습니다.'}
        ok, err = mr_resolver.set_manual(song, url, title=song.get('title'))
        return {'success': ok, 'view': 'mr_set_manual', 'error': err}

    def _mr_clear_manual(self):
        raw = self._arg('song', '')
        try:
            song = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {'success': False, 'view': 'mr_clear_manual', 'error': 'song 데이터가 올바른 JSON이 아닙니다.'}
        if not isinstance(song, dict):
            return {'success': False, 'view': 'mr_clear_manual', 'error': 'song 데이터가 올바르지 않습니다.'}
        removed = mr_resolver.clear_manual(song)
        return {'success': True, 'view': 'mr_clear_manual', 'removed': removed}

    def _add_custom_song(self):
        title = str(self._arg('title', '')).strip()
        singer = str(self._arg('singer', '')).strip()
        url = str(self._arg('url', '')).strip()
        if not title:
            return {'success': False, 'view': 'add_custom_song', 'error': '제목을 입력해주세요.'}
        number = 'C%d' % int(time.time() * 1000 % 100000000)
        song = {'brand': 'custom', 'no': number, 'title': title, 'singer': singer}
        from .persistent import upsert_songs
        upsert_songs('custom', [song])
        if url:
            ok, err = mr_resolver.set_manual(song, url, title=title)
            if not ok:
                return {'success': True, 'view': 'add_custom_song', 'song': song, 'warning': 'MR URL 등록 실패: ' + err}
        return {'success': True, 'view': 'add_custom_song', 'song': song}


    def _mr_diagnostics(self, db_type):
        return {'success': True, 'view': 'mr_diagnostics', 'diagnostics': mr_resolver.diagnostics(self._cfg(db_type))}

    def _queue_song(self):
        raw = self._arg('song', '')
        try:
            song = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {'success': False, 'error': 'song 데이터가 올바른 JSON이 아닙니다.'}
        if not isinstance(song, dict):
            return {'success': False, 'error': 'song 데이터가 올바르지 않습니다.'}
        requester = self._arg('requester', '')
        try:
            result = queue_store.add_song(song, requester=requester)
            # 버그 수정: start_next()는 이미 재생 중인 곡이 있으면 그 곡을 그대로 반환할
            # 뿐 아무것도 바꾸지 않는데, 예전 코드는 반환값이 있기만 하면(bool(current))
            # '이번 예약으로 새로 재생이 시작됐다'고 잘못 판단했다. 그 결과 이미 재생 중일
            # 때 예약 버튼을 누르면 프론트엔드가 재생 중인 곡을 다시 재생 요청해 처음부터
            # 재생되는 버그로 이어졌다. start_next() 호출 *전*에 이미 재생 중이던 곡이
            # 있었는지를 먼저 확인해, 그때 없었고 지금 있을 때만 '새로 시작됨'으로 판정한다.
            had_current_before = queue_store.has_active_current()
            current = queue_store.start_next()
            payload = queue_store.list_queue()
            return {
                'success': True,
                'view': 'queue_add',
                **result,
                **payload,
                'current': current or payload.get('current'),
                'autostarted': bool(current) and not had_current_before,
                'player': queue_store.get_player_state(),
            }
        except Exception as exc:
            return {'success': False, 'error': str(exc)}

    def _queue_action(self, action):
        try:
            if action in ('next', 'start'):
                row = queue_store.start_next()
                payload = queue_store.list_queue()
                return {'success': True, 'view': 'queue_next', **payload, 'current': row or payload.get('current'), 'player': queue_store.get_player_state()}
            if action == 'remove':
                ok = queue_store.remove(int(self._arg('id', '0')))
                return {'success': ok, 'view': 'queue_remove', 'removed': ok, **queue_store.list_queue(), 'error': '' if ok else '대기 중인 예약곡만 삭제할 수 있습니다.'}
            if action == 'clear':
                count = queue_store.clear_waiting()
                return {'success': True, 'view': 'queue_clear', 'removed': count, **queue_store.list_queue()}
            if action in ('finish','skip','cancel','error'):
                qid = int(self._arg('id', '0'))
                status = {'finish':'played','skip':'skipped','cancel':'cancelled','error':'error'}[action]
                row = queue_store.finish(qid, status=status, error=self._arg('error',''))
                # advance=0이면 다음 대기곡을 자동으로 이어받지 않는다.
                # (MR 미리듣기를 시작하기 전 현재 재생 곡만 정리할 때 사용 - 자동 진행을 켜두면
                #  대기열의 다음 곡까지 연쇄로 '재생 중' 상태가 되어 버려, 그 곡 역시 미리듣기에
                #  가려진 채 백엔드에만 재생 중으로 orphan되는 문제가 생긴다.)
                advance = self._arg('advance', '1') != '0'
                next_row = queue_store.start_next() if (row and advance) else None
                payload = queue_store.list_queue()
                return {
                    'success': bool(row),
                    'view': 'queue_finish',
                    'finished': row,
                    'next': next_row or payload.get('current'),
                    **payload,
                    'current': next_row or payload.get('current'),
                    'player': queue_store.get_player_state(),
                    'error': '' if row else '재생 중인 곡을 찾을 수 없습니다.',
                }
            if action == 'play_now':
                qid = int(self._arg('id', '0'))
                row = queue_store.play_now(qid)
                payload = queue_store.list_queue()
                return {
                    'success': bool(row),
                    'view': 'queue_play_now',
                    **payload,
                    'current': row or payload.get('current'),
                    'player': queue_store.get_player_state(),
                    'error': '' if row else '대기 중인 곡만 즉시 재생할 수 있습니다.',
                }
            if action == 'state':
                state = self._arg('state','idle')
                qid = self._arg('id','') or None
                queue_store.set_player_state(state, qid, self._arg('error',''))
                return {'success': True, 'view': 'player_state', 'player': queue_store.get_player_state(), **queue_store.list_queue()}
        except (TypeError, ValueError) as exc:
            return {'success': False, 'error': str(exc)}
        return {'success': False, 'error': '지원하지 않는 Queue 동작입니다.'}

    def _queue_payload(self):
        data = queue_store.list_queue()
        data.update({
            'success': True,
            'view': 'queue',
            'player': queue_store.get_player_state(),
            'summary': queue_store.queue_summary(),
        })
        return data

    def _history_payload(self):
        return {'success': True, 'view': 'history', 'history': queue_store.history(int(self._arg('limit','50') or 50))}

    def _history_clear(self):
        try:
            removed = queue_store.clear_history()
            return {'success': True, 'view': 'history_clear', 'removed': removed, 'history': []}
        except Exception as exc:
            return {'success': False, 'view': 'history_clear', 'error': str(exc)}

    def _history_log_preview(self):
        raw = self._arg('song', '')
        requester = self._arg('requester', '')
        try:
            song = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {'success': False, 'view': 'history_log_preview', 'error': 'song 데이터가 올바른 JSON이 아닙니다.'}
        if not isinstance(song, dict) or not song.get('title'):
            return {'success': False, 'view': 'history_log_preview', 'error': 'song 데이터가 올바르지 않습니다.'}
        try:
            queue_store.log_preview(song, requester=requester)
            return {'success': True, 'view': 'history_log_preview'}
        except Exception as exc:
            return {'success': False, 'view': 'history_log_preview', 'error': str(exc)}

    def _popular_payload(self):
        try:
            limit = int(self._arg('limit', '20') or 20)
        except (TypeError, ValueError):
            limit = 20
        rows = queue_store.popular_songs(limit)
        songs = []
        for r in rows:
            item = {
                'id': '%s:%s' % (r.get('brand') or '', r.get('no') or ''),
                'brand': normalize_brand(r.get('brand')),
                'no': r.get('no') or '',
                'title': r.get('title') or '',
                'singer': r.get('singer') or '',
                'brand_label': 'TJ' if normalize_brand(r.get('brand')) == BRAND_TJ else '금영' if normalize_brand(r.get('brand')) == BRAND_KY else (r.get('brand') or ''),
                'play_count': int(r.get('play_count') or 0),
            }
            songs.append(item)
        return {'success': True, 'view': 'popular', 'songs': songs}

    def _tj_chart_payload(self, db_type):
        chart_type = str(self._arg('chart_type', 'TOP') or 'TOP').upper()
        chart_type = 'HOT' if chart_type == 'HOT' else 'TOP'
        force = self._arg('force', '0') == '1'
        try:
            items, err = _tj_fetch_chart(chart_type=chart_type, force=force)
        except Exception as exc:
            return {'success': False, 'view': 'tj_chart', 'chart_type': chart_type, 'error': 'TJ 차트를 가져오는 중 오류: %s' % exc}
        if items is None:
            return {'success': False, 'view': 'tj_chart', 'chart_type': chart_type, 'error': err or 'TJ 차트를 가져오지 못했습니다.'}
        songs_all = load_songs(db_type).get('songs') or []
        catalog_numbers = {s.get('no') for s in songs_all if s.get('brand') == BRAND_TJ}

        def in_catalog(no):
            return no in catalog_numbers or no.zfill(6) in catalog_numbers or no.lstrip('0') in catalog_numbers

        songs = []
        for it in items:
            no = it['no']
            matched = in_catalog(no)
            meta = '%s위' % it['rank']
            if it.get('has_mv'):
                meta += ' · MV'
            if not matched:
                meta += ' · 카탈로그 미동기화'
            songs.append({
                'brand': BRAND_TJ,
                'no': no,
                'title': it['title'],
                'singer': it['singer'],
                'brand_label': 'TJ',
                'rank': it['rank'],
                '_meta': meta,
                '_catalog_matched': matched,
            })
        return {'success': True, 'view': 'tj_chart', 'chart_type': chart_type, 'songs': songs}

    def _ky_chart_payload(self, db_type):
        period = str(self._arg('period', '') or '')
        if period not in ('', 'w', 'm', 'y'):
            period = ''
        rng = '2' if str(self._arg('range', '1')) == '2' else '1'
        force = self._arg('force', '0') == '1'
        try:
            items, err = _ky_fetch_chart(period=period, rng=rng, force=force)
        except Exception as exc:
            return {'success': False, 'view': 'ky_chart', 'period': period, 'range': rng, 'error': 'KY 차트를 가져오는 중 오류: %s' % exc}
        if items is None:
            return {'success': False, 'view': 'ky_chart', 'period': period, 'range': rng, 'error': err or 'KY 차트를 가져오지 못했습니다.'}
        songs_all = load_songs(db_type).get('songs') or []
        catalog_numbers = {s.get('no') for s in songs_all if s.get('brand') == BRAND_KY}

        def in_catalog(no):
            return no in catalog_numbers or no.zfill(6) in catalog_numbers or no.lstrip('0') in catalog_numbers

        songs = []
        for it in items:
            no = it['no']
            matched = in_catalog(no)
            meta = '%s위' % it['rank']
            if it.get('youtube'):
                meta += ' · 유튜브'
            if not matched:
                meta += ' · 카탈로그 미동기화'
            songs.append({
                'brand': BRAND_KY,
                'no': no,
                'title': it['title'],
                'singer': it['singer'],
                'brand_label': '금영',
                'rank': it['rank'],
                '_meta': meta,
                '_catalog_matched': matched,
            })
        return {'success': True, 'view': 'ky_chart', 'period': period, 'range': rng, 'songs': songs}

    def get_dashboard_data(self, db_type, limit=80):
        view = str(self._arg('view', 'list') or 'list').strip().lower()
        if view == 'sync_status':
            return {'success': True, 'view': 'sync_status', 'sync': self._sync_snapshot()}
        if view == 'sync_start':
            return self._start_sync(self._arg('brand', BRAND_TJ), db_type, force_full=self._arg('full', '0') == '1')
        if view == 'sync_stop':
            return self._stop_sync()
        if view == 'youtube':
            return self._youtube_search(db_type)
        if view == 'mr_resolve':
            return self._mr_resolve(db_type)
        if view == 'mr_cache_clear':
            return self._mr_cache_clear()
        if view == 'export_seed':
            return self._export_seed(db_type)
        if view == 'mr_report_error':
            return self._mr_report_error(db_type)
        if view == 'mr_set_manual':
            return self._mr_set_manual()
        if view == 'mr_clear_manual':
            return self._mr_clear_manual()
        if view == 'add_custom_song':
            return self._add_custom_song()
        if view == 'mr_diagnostics':
            return self._mr_diagnostics(db_type)
        if view == 'queue':
            return self._queue_payload()
        if view == 'queue_summary':
            return {'success': True, 'view': 'queue_summary', 'summary': queue_store.queue_summary(), 'player': queue_store.get_player_state()}
        if view == 'queue_add':
            return self._queue_song()
        if view == 'queue_next' or view == 'queue_start':
            return self._queue_action('next')
        if view == 'queue_remove':
            return self._queue_action('remove')
        if view == 'queue_clear':
            return self._queue_action('clear')
        if view == 'queue_finish':
            return self._queue_action('finish')
        if view == 'queue_skip':
            return self._queue_action('skip')
        if view == 'queue_cancel':
            return self._queue_action('cancel')
        if view == 'queue_play_now':
            return self._queue_action('play_now')
        if view == 'queue_error':
            return self._queue_action('error')
        if view == 'player_state':
            return self._queue_action('state')
        if view == 'history':
            return self._history_payload()
        if view == 'history_clear':
            return self._history_clear()
        if view == 'history_log_preview':
            return self._history_log_preview()
        if view == 'popular':
            return self._popular_payload()
        if view == 'tj_chart':
            return self._tj_chart_payload(db_type)
        if view == 'ky_chart':
            return self._ky_chart_payload(db_type)
        return self._list_payload(db_type)
