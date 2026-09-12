# -*- coding: utf-8 -*-
"""노래방 곡 목록 저장소 (브랜드별 번호 upsert)."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time

_LOCK = threading.RLock()

BRAND_TJ = 'tj'
BRAND_KY = 'kumyoung'
BRAND_CUSTOM = 'custom'
BRAND_LABELS = {
    BRAND_TJ: 'TJ',
    BRAND_KY: '금영',
    BRAND_CUSTOM: '커스텀',
}

CHOSEONG = [
    'ㄱ', 'ㄲ', 'ㄴ', 'ㄷ', 'ㄸ', 'ㄹ', 'ㅁ', 'ㅂ', 'ㅃ',
    'ㅅ', 'ㅆ', 'ㅇ', 'ㅈ', 'ㅉ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ',
]
CHOSEONG_COLLAPSE = {
    'ㄲ': 'ㄱ',
    'ㄸ': 'ㄷ',
    'ㅃ': 'ㅂ',
    'ㅆ': 'ㅅ',
    'ㅉ': 'ㅈ',
}
KOREAN_KEYS = ['ㄱ', 'ㄴ', 'ㄷ', 'ㄹ', 'ㅁ', 'ㅂ', 'ㅅ', 'ㅇ', 'ㅈ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ']
KOREAN_LABELS = {
    'ㄱ': '가', 'ㄴ': '나', 'ㄷ': '다', 'ㄹ': '라', 'ㅁ': '마', 'ㅂ': '바',
    'ㅅ': '사', 'ㅇ': '아', 'ㅈ': '자', 'ㅊ': '차', 'ㅋ': '카', 'ㅌ': '타',
    'ㅍ': '파', 'ㅎ': '하',
}


def plugin_root():
    return os.path.dirname(os.path.abspath(__file__))


def data_dir():
    override = str(os.environ.get('KARAOKE_SINGER_DATA_DIR') or '').strip()
    path = override or os.path.join(plugin_root(), 'data')
    os.makedirs(path, exist_ok=True)
    return path


def songs_path():
    return os.path.join(data_dir(), 'songs.json')


def meta_path():
    return os.path.join(data_dir(), 'sync_meta.json')


def _load_meta():
    path = meta_path()
    with _LOCK:
        if not os.path.isfile(path):
            return {'updated': {}, 'auto_sync_date': ''}
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return {'updated': {}, 'auto_sync_date': ''}
    if not isinstance(payload, dict):
        return {'updated': {}, 'auto_sync_date': ''}
    updated = payload.get('updated')
    return {
        'updated': updated if isinstance(updated, dict) else {},
        'auto_sync_date': str(payload.get('auto_sync_date') or ''),
    }


def _save_meta(meta):
    with _LOCK:
        _atomic_write_json(meta_path(), {
            'updated': meta.get('updated') or {},
            'auto_sync_date': str(meta.get('auto_sync_date') or ''),
        })


def _set_updated_at(brand, stamp):
    if not stamp:
        return
    meta = _load_meta()
    meta.setdefault('updated', {})[normalize_brand(brand)] = stamp
    _save_meta(meta)


def get_auto_sync_date():
    """자동(백그라운드) 동기화가 마지막으로 실행된 날짜(YYYY-MM-DD)를 반환한다.

    서버가 하루에 여러 번 재시작되어도 자동 동기화가 같은 날 중복 실행되지 않도록
    날짜만 기록해둔다.
    """
    return _load_meta().get('auto_sync_date') or ''


def set_auto_sync_date(date_str):
    meta = _load_meta()
    meta['auto_sync_date'] = str(date_str or '')
    _save_meta(meta)


def normalize_brand(value):
    raw = str(value or '').strip().lower()
    if raw in ('ky', 'kumyoung', '금영', 'geumyoung'):
        return BRAND_KY
    if raw in ('tj', '태진'):
        return BRAND_TJ
    return raw


def song_key(brand, number):
    return '%s:%s' % (normalize_brand(brand), str(number or '').strip())


def title_first_key(title):
    text = str(title or '').strip()
    if not text:
        return '#'
    ch = text[0]
    if ch.isdigit():
        return ch
    if ch.isascii() and ch.isalpha():
        return ch.upper()
    code = ord(ch)
    if 0xAC00 <= code <= 0xD7A3:
        choseong = CHOSEONG[(code - 0xAC00) // 588]
        return CHOSEONG_COLLAPSE.get(choseong, choseong)
    return '#'


def key_category(first_key):
    token = str(first_key or '')
    if token.isdigit():
        return 'digit'
    if len(token) == 1 and 'A' <= token <= 'Z':
        return 'latin'
    if token in KOREAN_LABELS:
        return 'korean'
    return 'special'


def annotate_song(row):
    item = dict(row or {})
    item['brand'] = normalize_brand(item.get('brand'))
    item['no'] = str(item.get('no') or '').strip()
    item['title'] = str(item.get('title') or '').strip()
    item['singer'] = str(item.get('singer') or '').strip()
    item['title_first_key'] = title_first_key(item['title'])
    item['title_first_category'] = key_category(item['title_first_key'])
    item['brand_label'] = BRAND_LABELS.get(item['brand'], item['brand'] or '')
    # 검색어를 미리 소문자로 합쳐 캐시해둔다 - 매 검색 요청마다 문자열을 다시 합치고
    # lower()를 반복하는 비용을 없애기 위함(11만곡 이상에서 체감 가능한 차이가 남).
    item['_search_blob'] = ' '.join([item['title'], item['singer'], item['no'], item['brand_label']]).lower()
    return item


def _empty_payload():
    return {'songs': [], 'updated': {}}


_SEED_CHECKED = False


def seed_path():
    """플러그인 폴더에 함께 배포되는 번들 시드 파일 경로.

    data_dir()(런타임 데이터, 보통 plugin_root()/data)와는 별개로 플러그인 코드와 같은
    위치에 둔다 - 시드는 "동기화 결과 스냅샷"이고, data/ 안의 나머지 파일(예약 큐, MR 캐시,
    차단 목록 등)은 사용자별 개인 상태라 함께 배포하면 안 되기 때문이다.
    """
    return os.path.join(plugin_root(), 'songs_seed.json')


def export_seed(db_type='general'):
    """현재 곡 목록을 번들 시드 파일로 내보낸다.

    이 파일이 포함된 채로 플러그인 폴더를 재배포하면, 새로 설치하는 사용자는
    동기화 없이 바로 전체 목록을 쓸 수 있다(import_seed_if_empty 참고).
    """
    payload = load_songs(db_type)
    songs = payload.get('songs') or []
    body = {'songs': songs, 'count': len(songs)}
    path = seed_path()
    directory = os.path.dirname(path) or '.'
    fd, tmp = tempfile.mkstemp(prefix='.songs-seed-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(body, fh, ensure_ascii=False, separators=(',', ':'))
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
    return {'path': path, 'count': len(songs)}


def import_seed_if_empty():
    """새로 설치된 인스턴스(곡 데이터가 비어 있음)에서 번들 시드가 있으면 자동으로 채운다.

    프로세스당 한 번만 확인한다(이미 곡이 있으면 아무것도 하지 않고, 절대 기존 데이터를
    덮어쓰지 않는다).
    """
    global _SEED_CHECKED
    if _SEED_CHECKED:
        return False
    _SEED_CHECKED = True
    existing = _load_json_songs()
    if existing.get('songs'):
        return False
    path = seed_path()
    if not os.path.isfile(path):
        return False
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return False
    songs = data.get('songs') if isinstance(data, dict) else None
    if not songs:
        return False
    save_songs({'songs': songs, 'updated': {}})
    return True


def _sort_key(item):
    return (item.get('title') or '', item.get('no') or '', item.get('brand') or '')


_JSON_CACHE = {'key': None, 'payload': None}


def _load_json_songs():
    path = songs_path()
    with _LOCK:
        if not os.path.isfile(path):
            return _empty_payload()
        try:
            st = os.stat(path)
        except OSError:
            return _empty_payload()
        # 파일이 바뀌지 않았으면(mtime+size 동일) 디스크 재읽기/재파싱/재정규화를 건너뛰고
        # 메모리 캐시를 그대로 돌려준다. 카테고리 이동·페이지 이동·검색처럼 데이터가
        # 바뀌지 않는 조작에서 매번 수십MB짜리 JSON을 다시 읽는 게 느림의 주 원인이었다.
        cache_key = (st.st_mtime_ns, st.st_size)
        cached = _JSON_CACHE
        if cached['key'] == cache_key and cached['payload'] is not None:
            return cached['payload']
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return _empty_payload()
        if isinstance(payload, list):
            payload = {'songs': payload, 'updated': {}}
        songs = [annotate_song(row) for row in (payload.get('songs') or []) if isinstance(row, dict)]
        # 제목 기준 가나다(유니코드 한글 음절 순서가 그대로 사전순과 일치) / ABC / 번호 순으로
        # 한 번만 정렬해서 캐시해둔다 - 목록/검색/카테고리 어느 화면에서도 일관되게 정렬된
        # 상태로 나가고, 매 요청마다 다시 정렬할 필요가 없다.
        songs.sort(key=_sort_key)
        result = {
            'songs': songs,
            'updated': payload.get('updated') if isinstance(payload.get('updated'), dict) else {},
        }
        _JSON_CACHE['key'] = cache_key
        _JSON_CACHE['payload'] = result
        return result


def _gateway(db_type='general'):
    try:
        from services.plugin_db_gateway import PluginDatabaseGateway
        return PluginDatabaseGateway(db_type or 'general')
    except Exception:
        return None


_DB_ROWS_CACHE = {'db_type': None, 'at': 0.0, 'rows': None}
_DB_ROWS_CACHE_TTL = 5.0  # seconds


def _db_rows(db_type='general'):
    now = time.time()
    cached = _DB_ROWS_CACHE
    if cached['db_type'] == db_type and cached['rows'] is not None and (now - cached['at']) < _DB_ROWS_CACHE_TTL:
        return cached['rows']
    gateway = _gateway(db_type)
    if gateway is None:
        return None
    try:
        rows = gateway.fetch_all('SELECT * FROM plugin_karaoke_songs')
    except Exception:
        return None
    if rows is None:
        return None
    def pick(row, names, default=''):
        lowered = {str(k).lower(): v for k, v in dict(row).items()}
        for name in names:
            if name.lower() in lowered:
                return lowered[name.lower()]
        return default
    out=[]
    for row in rows:
        item={
            'id': pick(row, ('id','song_id')),
            'brand': pick(row, ('brand','brand_code','maker','company','machine','brand_name')),
            'no': pick(row, ('no','number','song_no','song_number','songnum')),
            'title': pick(row, ('title','song_title','name')),
            'singer': pick(row, ('singer','artist','vocal','singer_name')),
            'composer': pick(row, ('composer','composer_name')),
            'lyricist': pick(row, ('lyricist','lyricist_name','lyrics_writer')),
            'release': pick(row, ('release','release_date','released_at')),
        }
        if item['no'] and item['title']:
            out.append(annotate_song(item))
    # JSON 경로와 동일하게 제목 기준으로 정렬해둔다.
    out.sort(key=_sort_key)
    _DB_ROWS_CACHE['db_type'] = db_type
    _DB_ROWS_CACHE['at'] = now
    _DB_ROWS_CACHE['rows'] = out
    return out


_SEED_DB_CHECKED = False


def import_seed_to_db_if_empty(db_type='general'):
    """DB 모드인데 plugin_karaoke_songs 테이블이 비어 있는 경우(신규 설치 직후 등)에도
    번들 시드를 자동으로 채운다.

    import_seed_if_empty()는 DB 자체를 못 쓰는 환경(JSON 폴백)만 처리하는데, 실제로는
    BookOasis가 테이블은 미리 만들어두고 데이터만 비어 있는 경우가 있어 그 경로를 못 타는
    문제가 있었다. 이 함수가 그 빈틈을 메운다.

    시드가 수만~수십만 곡이라 매 행마다 SELECT로 중복 확인하면 너무 느려지므로, 테이블이
    확실히 비어 있는(이 함수를 호출하기 직전에 확인한) 상황에서만 중복 확인 없이 그대로
    INSERT한다. 백그라운드 스레드에서 돌려서 첫 화면 로딩을 막지 않는다.
    """
    global _SEED_DB_CHECKED
    if _SEED_DB_CHECKED:
        return False
    _SEED_DB_CHECKED = True
    gateway = _gateway(db_type)
    if gateway is None:
        return False
    try:
        existing = gateway.fetch_one('SELECT 1 AS x FROM plugin_karaoke_songs LIMIT 1')
    except Exception:
        return False
    if existing:
        return False  # 이미 데이터가 있으면 절대 건드리지 않는다.
    path = seed_path()
    if not os.path.isfile(path):
        return False
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return False
    songs = data.get('songs') if isinstance(data, dict) else None
    if not songs:
        return False

    def _run():
        try:
            _bulk_insert_seed_rows(gateway, songs, db_type)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True, name='karaoke-seed-db-import').start()
    return True


def _bulk_insert_seed_rows(gateway, songs, db_type='general'):
    cols = _db_columns(db_type)
    if not cols:
        return 0
    lower = {c.lower(): c for c in cols}

    def col(names):
        for n in names:
            if n.lower() in lower:
                return lower[n.lower()]
        return None

    c_brand = col(('brand', 'brand_code', 'maker', 'company', 'machine', 'brand_name'))
    c_no = col(('no', 'number', 'song_no', 'song_number', 'songnum'))
    c_title = col(('title', 'song_title', 'name'))
    c_singer = col(('singer', 'artist', 'vocal', 'singer_name'))
    c_composer = col(('composer', 'composer_name'))
    c_lyricist = col(('lyricist', 'lyricist_name', 'lyrics_writer'))
    if not (c_brand and c_no and c_title):
        return 0
    optional = [c_singer, c_composer, c_lyricist]
    insert_cols = [c_brand, c_no, c_title] + [x for x in optional if x]
    placeholders = ','.join(['?'] * len(insert_cols))
    names = ','.join('`%s`' % x for x in insert_cols)
    sql = 'INSERT INTO plugin_karaoke_songs (%s) VALUES (%s)' % (names, placeholders)
    added = 0
    for raw in songs:
        item = dict(raw or {})
        item['brand'] = normalize_brand(item.get('brand'))
        vals = [item.get('brand', ''), item.get('no', ''), item.get('title', '')]
        for c in optional:
            if c:
                vals.append(item.get('singer' if c == c_singer else 'composer' if c == c_composer else 'lyricist', ''))
        try:
            gateway.execute(sql, tuple(vals))
            added += 1
        except Exception:
            continue
    return added


def load_songs(db_type='general'):
    # 기존 DB를 단일 원본으로 사용한다. DB를 사용할 수 없는 환경에서만 JSON으로 폴백한다.
    rows = _db_rows(db_type)
    if rows is not None:
        if not rows:
            # DB 테이블은 있지만(BookOasis가 미리 만들어둠) 비어 있는 신규 설치 상황일 수
            # 있으니, 번들 시드가 있으면 백그라운드에서 채워본다. 이 요청 자체는 그대로
            # 빈 목록을 반환하고, 다음 새로고침부터 채워진 목록이 보인다.
            import_seed_to_db_if_empty(db_type)
        return {'songs': rows, 'updated': _load_meta().get('updated') or {}}
    payload = _load_json_songs()
    if not payload.get('songs'):
        # 새로 설치된 인스턴스일 수 있으니, 번들 시드가 있으면 한 번 채워보고 다시 읽는다.
        if import_seed_if_empty():
            payload = _load_json_songs()
    return payload


def _db_columns(db_type='general'):
    gateway = _gateway(db_type)
    if gateway is None:
        return []
    for q in (
        'DESCRIBE plugin_karaoke_songs',
        'PRAGMA table_info(plugin_karaoke_songs)',
    ):
        try:
            rows=gateway.fetch_all(q) or []
            cols=[]
            for r in rows:
                d=dict(r)
                name=d.get('Field') or d.get('field') or d.get('name') or d.get('Name')
                if name: cols.append(str(name))
            if cols: return cols
        except Exception:
            continue
    return []


def upsert_songs_db(brand, rows, db_type='general'):
    gateway=_gateway(db_type)
    if gateway is None or not rows:
        return 0
    cols=_db_columns(db_type)
    if not cols: return 0
    lower={c.lower():c for c in cols}
    def col(names):
        for n in names:
            if n.lower() in lower: return lower[n.lower()]
        return None
    c_brand=col(('brand','brand_code','maker','company','machine','brand_name'))
    c_no=col(('no','number','song_no','song_number','songnum'))
    c_title=col(('title','song_title','name'))
    c_singer=col(('singer','artist','vocal','singer_name'))
    c_composer=col(('composer','composer_name'))
    c_lyricist=col(('lyricist','lyricist_name','lyrics_writer'))
    if not (c_brand and c_no and c_title): return 0
    optional=[c_singer,c_composer,c_lyricist]
    insert_cols=[c_brand,c_no,c_title]+[x for x in optional if x]
    placeholders=','.join(['?']*len(insert_cols))
    names=','.join('`%s`'%x for x in insert_cols)
    params=[]
    for raw in rows:
        item=annotate_song(raw); item['brand']=normalize_brand(brand)
        vals=[item.get('brand',''),item.get('no',''),item.get('title','')]
        for c in optional:
            if c: vals.append(item.get('singer' if c==c_singer else 'composer' if c==c_composer else 'lyricist',''))
        params.append(tuple(vals))
    # Avoid duplicates by checking brand/no before insert.
    added=0
    for vals in params:
        try:
            existing=gateway.fetch_one('SELECT 1 AS x FROM plugin_karaoke_songs WHERE `%s`=? AND `%s`=? LIMIT 1' % (c_brand,c_no), (vals[0],vals[1]))
            if existing: continue
            gateway.execute('INSERT INTO plugin_karaoke_songs (%s) VALUES (%s)'%(names,placeholders), vals)
            added += 1
        except Exception:
            continue
    return added


def _atomic_write_json(path, body):
    directory = os.path.dirname(path) or '.'
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.songs-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(body, fh, ensure_ascii=False, indent=0)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def save_songs(payload):
    path = songs_path()
    body = {
        'songs': [annotate_song(row) for row in (payload.get('songs') or [])],
        'updated': payload.get('updated') if isinstance(payload.get('updated'), dict) else {},
    }
    with _LOCK:
        _atomic_write_json(path, body)
    return body

def known_numbers(songs, brand):
    want = normalize_brand(brand)
    return {
        str(row.get('no') or '').strip()
        for row in songs
        if normalize_brand(row.get('brand')) == want and str(row.get('no') or '').strip()
    }


def upsert_songs(brand, rows, updated_at=None, db_type='general'):
    """같은 브랜드의 기존 번호는 건너뛰고, 다른 브랜드 곡은 그대로 둔다."""
    want = normalize_brand(brand)
    # 버그 수정: 반드시 실제 동기화 대상 db_type 기준으로 기존곡을 조회해야 한다.
    # (이전에는 인자 없이 load_songs()를 호출해 항상 'general' DB 기준으로만
    #  중복을 판단했고, 그 결과 다른 db_type에서는 이미 있는 곡도 added로 잘못
    #  집계되고 songs.json에 중복 항목이 쌓였다.)
    payload = load_songs(db_type)
    songs = list(payload.get('songs') or [])
    known = known_numbers(songs, want)
    added = []
    skipped = 0
    for raw in rows or []:
        item = annotate_song(raw)
        item['brand'] = want
        number = item.get('no') or ''
        if not number:
            continue
        if number in known:
            skipped += 1
            continue
        known.add(number)
        added.append(item)
        songs.append(item)

    gateway_active = _gateway(db_type) is not None
    if added:
        try:
            upsert_songs_db(want, added, db_type=db_type)
        except Exception:
            gateway_active = False

    if gateway_active:
        # DB가 원본(source of truth)인 경우: 매 페이지마다 전체 songs.json을
        # 통째로 재작성하지 않고, 갱신 시각만 가벼운 메타 파일에 기록한다.
        # (이전에는 동기화 페이지마다 전체 곡 목록을 JSON으로 덤프해 파일이
        #  선형으로 계속 커지고 쓰기 비용이 O(n^2)에 가깝게 늘어났다.)
        if updated_at:
            _set_updated_at(want, updated_at)
    else:
        # DB를 사용할 수 없는 환경: JSON이 유일한 원본이므로 그대로 저장한다.
        if updated_at:
            payload.setdefault('updated', {})
            payload['updated'][want] = updated_at
        payload['songs'] = songs
        if added or updated_at:
            save_songs(payload)

    return {
        'added': len(added),
        'skipped': skipped,
        'total': len(songs),
        'brand_total': len(known_numbers(songs, want)),
    }


def present_keys(songs, category=None):
    buckets = {
        'digit': [],
        'latin': [],
        'korean': [],
        'special': [],
    }
    seen = {name: set() for name in buckets}
    for row in songs or []:
        key = row.get('title_first_key') or title_first_key(row.get('title'))
        cat = key_category(key)
        if key in seen[cat]:
            continue
        seen[cat].add(key)
        buckets[cat].append(key)
    buckets['digit'] = sorted(buckets['digit'])
    buckets['latin'] = sorted(buckets['latin'])
    buckets['korean'] = [key for key in KOREAN_KEYS if key in seen['korean']]
    buckets['special'] = sorted(buckets['special'])
    if category:
        return {category: buckets.get(category) or []}
    return buckets
