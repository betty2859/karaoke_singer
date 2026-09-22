# -*- coding: utf-8 -*-
"""TJ / 금영 곡 목록 증분 동기화 (manana.kr API, urllib only)."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .persistent import BRAND_KY, BRAND_TJ, normalize_brand, upsert_songs

API_URL = 'https://api.manana.kr/v2/karaoke/search.json'
LATEST_URL = 'https://api.manana.kr/karaoke/%s.json'
USER_AGENT = 'BookOasis-karaoke_singer/2.3.1'
REQUEST_TIMEOUT = 20
PAGE_LIMIT = 50
EARLY_STOP_PAGES = 2
EARLY_STOP_MIN_EXISTING = 100
# manana.kr treats no=0 as empty, so prefix 0 dumps the whole catalog and 500s with orderBy.
DIGIT_PREFIXES = [str(n) for n in range(1, 10)]
HTTP_RETRIES = 3


def search_url(brand, digit, page, limit=PAGE_LIMIT):
    params = {
        'brand': normalize_brand(brand),
        'no': str(digit),
        'noLikeSide': 'both',
        'limit': str(int(limit)),
        'page': str(int(page)),
    }
    return '%s?%s' % (API_URL, urllib.parse.urlencode(params))


def latest_url(brand):
    return LATEST_URL % normalize_brand(brand)


def _http_get_json(url):
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': USER_AGENT,
            'Accept': 'application/json',
        },
    )
    last_error = None
    response_status = 0
    for attempt in range(HTTP_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                response_status = int(getattr(resp, 'status', 200) or 200)
                raw = resp.read().decode('utf-8', errors='replace')
            break
        except urllib.error.HTTPError as exc:
            last_error = RuntimeError('karaoke API HTTP %s' % exc.code)
            if exc.code < 500 or attempt >= HTTP_RETRIES - 1:
                raise last_error from exc
            time.sleep(0.4 * (attempt + 1))
        except urllib.error.URLError as exc:
            raise RuntimeError('karaoke API 연결 실패: %s' % exc.reason) from exc
    else:
        raise last_error or RuntimeError('karaoke API 요청 실패')
    if raw.lstrip().startswith('<'):
        raise RuntimeError('karaoke API가 JSON 대신 HTML을 반환했습니다 (HTTP %s)' % (response_status or '응답'))
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError('karaoke API JSON 파싱 실패') from exc


def _rows_from_payload(payload):
    if isinstance(payload, list):
        return payload, len(payload)
    if not isinstance(payload, dict):
        return [], 0
    rows = payload.get('data')
    if not isinstance(rows, list):
        rows = []
    total_row = 0
    total = payload.get('total') or {}
    try:
        total_row = int((total.get('row') if isinstance(total, dict) else 0) or 0)
    except (TypeError, ValueError):
        total_row = 0
    return rows, total_row


def fetch_latest(brand):
    payload = _http_get_json(latest_url(brand))
    rows, total_row = _rows_from_payload(payload)
    want = normalize_brand(brand)
    rows = [row for row in rows if isinstance(row, dict) and normalize_brand(row.get('brand') or want) == want]
    return rows, total_row or len(rows)


def fetch_page(brand, digit, page, limit=PAGE_LIMIT):
    payload = _http_get_json(search_url(brand, digit, page, limit=limit))
    rows, total_row = _rows_from_payload(payload)
    want = normalize_brand(brand)
    rows = [row for row in rows if isinstance(row, dict) and normalize_brand(row.get('brand') or want) == want]
    return rows, total_row


def row_to_song(row, brand):
    item = row if isinstance(row, dict) else {}
    number = str(item.get('no') or '').strip()
    if not number:
        return None
    return {
        'brand': normalize_brand(brand),
        'no': number,
        'title': str(item.get('title') or '').strip(),
        'singer': str(item.get('singer') or '').strip(),
        'composer': str(item.get('composer') or '').strip(),
        'lyricist': str(item.get('lyricist') or '').strip(),
        'release': str(item.get('release') or '').strip(),
    }


def _ingest(brand, rows, added_total, skipped_total, db_type='general'):
    songs = []
    for row in rows or []:
        item = row_to_song(row, brand)
        if item:
            songs.append(item)
    if not songs:
        return added_total, skipped_total, 0
    result = upsert_songs(brand, songs, db_type=db_type)
    added = int(result.get('added') or 0)
    skipped = int(result.get('skipped') or 0)
    return added_total + added, skipped_total + skipped, added


def sync_brand(
    brand,
    existing_count=0,
    stop_event=None,
    progress_cb=None,
    fetch=fetch_page,
    fetch_latest_fn=fetch_latest,
    db_type='general',
    force_full=False,
):
    """번호가 이미 있으면 건너뛰고 신규만 추가. 연속 기지 페이지면 그 번호대만 건너뛴다.

    force_full=True 이면 조기 종료 없이 1~9로 시작하는 모든 번호대를 끝까지 훑는다.
    (중간에 비어있는 번호가 새로 채워지는 등, 끝에만 신규가 붙는다는 가정이 깨지는
    경우를 대비한 전체 재동기화용.)
    """
    want = normalize_brand(brand)
    if want not in (BRAND_TJ, BRAND_KY):
        raise ValueError('지원하지 않는 브랜드: %s' % brand)
    added_total = 0
    skipped_total = 0
    stopped = False
    early_stop_hit = False
    label = 'TJ' if want == BRAND_TJ else '금영'

    def stopped_now():
        return bool(stop_event is not None and stop_event.is_set())

    if callable(progress_cb):
        progress_cb({
            'brand': want,
            'digit': '',
            'page': 0,
            'added': added_total,
            'skipped': skipped_total,
            'message': '%s 최신곡을 가져오는 중…' % label,
        })
    latest_rows, _total = fetch_latest_fn(want)
    added_total, skipped_total, _added = _ingest(want, latest_rows, added_total, skipped_total, db_type=db_type)

    for digit in DIGIT_PREFIXES:
        if stopped_now():
            stopped = True
            break
        page = 1
        consecutive_known = 0
        while True:
            if stopped_now():
                stopped = True
                break
            if callable(progress_cb):
                progress_cb({
                    'brand': want,
                    'digit': digit,
                    'page': page,
                    'added': added_total,
                    'skipped': skipped_total,
                    'message': '%s 번호 %s… 페이지 %s%s' % (label, digit, page, ' (전체 동기화)' if force_full else ''),
                })
            rows, total_row = fetch(want, digit, page)
            songs_count = len([row for row in rows if row_to_song(row, want)])
            added_total, skipped_total, added = _ingest(want, rows, added_total, skipped_total, db_type=db_type)
            if songs_count <= 0:
                break
            if added <= 0:
                consecutive_known += 1
            else:
                consecutive_known = 0
            if (
                not force_full
                and existing_count >= EARLY_STOP_MIN_EXISTING
                and consecutive_known >= EARLY_STOP_PAGES
            ):
                early_stop_hit = True
                if callable(progress_cb):
                    progress_cb({
                        'brand': want,
                        'digit': digit,
                        'page': page,
                        'added': added_total,
                        'skipped': skipped_total,
                        'message': '%s 번호 %s대는 기존 번호만 나와 다음 번호대로 넘어갑니다.' % (label, digit),
                    })
                # 버그 수정: 이전에는 여기서 전체 동기화를 즉시 종료(return)해서 나머지
                # 번호대(2~9)를 아예 확인하지 않았다. 이제는 이 번호대만 건너뛰고
                # 다음 번호대(digit)로 계속 진행한다.
                break
            if songs_count < PAGE_LIMIT:
                break
            if total_row and page * PAGE_LIMIT >= total_row:
                break
            page += 1
            time.sleep(0.05)
    return {
        'brand': want,
        'added': added_total,
        'skipped': skipped_total,
        'early_stop': early_stop_hit,
        'stopped': stopped,
    }
