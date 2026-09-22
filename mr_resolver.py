# -*- coding: utf-8 -*-
"""노래방 MR 탐색기.

우선순위:
1) MR 캐시
2) 설정된 로컬 미디어 경로
3) yt-dlp YouTube 검색
4) YouTube Data API fallback

yt-dlp는 검색/메타데이터 확인에 사용하고, 실제 YouTube 재생은 기존 IFrame Player를 사용한다.
이렇게 하면 YouTube Data API 장애/쿼터 문제와 플레이어 문제를 분리할 수 있다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from . import queue_store

YOUTUBE_SEARCH = 'https://www.googleapis.com/youtube/v3/search'
DEFAULT_TIMEOUT = 20
VIDEO_EXTS = {'.mp3', '.m4a', '.aac', '.ogg', '.opus', '.flac', '.wav', '.mp4', '.mkv', '.webm'}
CDG_EXTS = {'.cdg'}


def _norm(value):
    text = str(value or '').lower().strip()
    text = re.sub(r'\[[^\]]*\]|\([^)]*\)|\{[^}]*\}', ' ', text)
    text = re.sub(r'[^0-9a-z가-힣]+', '', text)
    return text


def _tokens(value):
    raw = str(value or '').lower().strip()
    return [x for x in re.split(r'[^0-9a-z가-힣]+', raw) if len(x) >= 2]


_NON_MR_MARKERS = (
    'live', '라이브', 'official video', 'music video', 'mv', 'shorts',
    '직캠', '방송', 'cover', '커버',
)


def _candidate_is_allowed(title, channel=''):
    """명백히 실황/뮤직비디오/커버인 후보만 제외한다.

    가사 영상은 반주 검색 결과가 없을 때 재생 가능한 fallback이 될 수 있으므로
    여기서 제거하지 않는다. 대신 _score_title에서 반주 후보보다 낮게 정렬한다.
    """
    text = ('%s %s' % (title or '', channel or '')).lower()
    return not any(marker in text for marker in _NON_MR_MARKERS)


def _score_title(title, singer, candidate, brand='', number=''):
    target = _norm('%s %s' % (title, singer))
    c = str(candidate or '').lower()
    cn = _norm(candidate)
    score = 0
    if target and target in cn:
        score += 80
    for token in _tokens(title):
        if token in c:
            score += 18
    for token in _tokens(singer):
        if token in c:
            score += 15
    positives = ('mr', 'inst', 'instrumental', 'karaoke', '반주', '노래방', '노래방반주', '가라오케', 'minus one')
    negatives = ('live', '라이브', 'official video', 'music video', 'mv', 'lyrics', '가사', '해석', '발음', 'cover', '커버', 'shorts', '직캠', '방송')
    for p in positives:
        if p in c:
            score += 12
    for n in negatives:
        if n in c:
            score -= 18
    if number and re.search(r'(?<!\d)%s(?!\d)' % re.escape(str(number)), c):
        score += 45
    # 금영(KY) 우선, TJ는 차선 - 실사용 결과 TJ 공식 MR은 임베드 차단이 압도적으로 많고
    # 금영 쪽은 상대적으로 재생 가능한 경우가 많다는 게 여러 유사 프로젝트에서도 확인된
    # 경향이라, 같은 조건이면 금영 계열 채널/제목을 먼저 시도하도록 가중치를 다르게 준다.
    # (특정 브랜드를 배제하는 게 아니라 "시도 순서"만 조정하는 것 - TJ 후보도 그대로 남아있고
    # 금영 쪽이 없거나 막히면 자연스럽게 TJ 쪽이 다음 순번으로 시도된다.)
    ky_markers = ('금영', 'kumyoung', 'ky노래방', 'ky karaoke')
    tj_markers = ('tj', 'tj노래방', 'tj karaoke')
    if any(m in c for m in ky_markers):
        score += 16
    elif any(m in c for m in tj_markers):
        score += 6
    return score


def _cache_key(song):
    # 예약 대기열의 id는 queue row id이고, MR 캐시는 catalog song_id 기준이어야 한다.
    # queue row에는 song_id와 id가 함께 있으므로 song_id를 먼저 사용한다.
    return str(song.get('song_id') or song.get('id') or '%s:%s' % (song.get('brand') or '', song.get('no') or '')).strip()


def _cache_matches_song(song, cached):
    """오래된 queue row id 캐시가 다른 곡에 재사용되지 않게 확인한다."""
    target_title = _norm(song.get('title'))
    cached_text = '%s %s' % (cached.get('title') or '', cached.get('channel') or '')
    cached_norm = _norm(cached_text)
    if not target_title or not cached_norm:
        return True
    if target_title in cached_norm or cached_norm in target_title:
        return True
    number = str(song.get('no') or '').strip()
    if number and re.search(r'(?<!\d)%s(?!\d)' % re.escape(number), cached_text):
        return True
    tokens = _tokens('%s %s' % (song.get('title') or '', song.get('singer') or ''))
    return not tokens or any(token in cached_norm for token in tokens)


def _youtube_watch(video_id):
    return 'https://www.youtube.com/watch?v=%s' % video_id


def _youtube_embed(video_id):
    return 'https://www.youtube.com/embed/%s?enablejsapi=1&playsinline=1' % video_id


def _result(source, status='ok', **kwargs):
    data = {'source': source, 'status': status}
    data.update(kwargs)
    return data


def _local_roots(config):
    raw = str(config.get('MR_LOCAL_PATHS') or '').strip()
    roots = []
    for part in re.split(r'[;\n]+', raw):
        path = os.path.abspath(os.path.expanduser(part.strip())) if part.strip() else ''
        if path and os.path.isdir(path) and path not in roots:
            roots.append(path)
    return roots


def _local_public_url(path, config):
    raw = str(config.get('MR_LOCAL_PUBLIC_MAPS') or '').strip()
    for part in re.split(r'[;\n]+', raw):
        if '=' not in part:
            continue
        local_root, public_root = part.split('=', 1)
        local_root = os.path.abspath(os.path.expanduser(local_root.strip()))
        public_root = public_root.strip().rstrip('/')
        try:
            rel = os.path.relpath(path, local_root)
        except ValueError:
            continue
        if rel == '..' or rel.startswith('..' + os.sep):
            continue
        return public_root + '/' + urllib.parse.quote(rel.replace(os.sep, '/'))
    return ''


def _local_search(song, config):
    roots = _local_roots(config)
    if not roots:
        return None, {'status': 'disabled', 'message': 'MR_LOCAL_PATHS가 설정되지 않았습니다.'}

    title = str(song.get('title') or '').strip()
    singer = str(song.get('singer') or '').strip()
    no = str(song.get('no') or '').strip()
    brand = str(song.get('brand') or '').strip()
    target_tokens = [_norm(x) for x in (title, singer, no) if x]
    best = None
    best_score = -9999
    scanned = 0
    max_files = int(config.get('MR_LOCAL_MAX_FILES') or 60000)

    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                scanned += 1
                if scanned > max_files:
                    break
                stem, ext = os.path.splitext(name)
                ext = ext.lower()
                if ext not in VIDEO_EXTS:
                    continue
                normalized = _norm(name)
                score = 0
                if no and _norm(no) and _norm(no) in normalized:
                    score += 70
                if title and _norm(title) and _norm(title) in normalized:
                    score += 60
                if singer and _norm(singer) and _norm(singer) in normalized:
                    score += 35
                if brand and _norm(brand) in normalized:
                    score += 10
                score += _score_title(title, singer, name) // 2
                if score > best_score and score >= 55:
                    full = os.path.join(dirpath, name)
                    best_score = score
                    best = {
                        'source': 'local',
                        'status': 'ok',
                        'media_type': 'video' if ext in {'.mp4', '.mkv', '.webm'} else 'audio',
                        'path': full,
                        'media_url': _local_public_url(full, config),
                        'filename': name,
                        'score': score,
                        'label': name,
                    }
            if scanned > max_files:
                break
        if scanned > max_files:
            break
    if best:
        return best, {'status': 'ok', 'scanned': scanned, 'roots': roots}
    return None, {'status': 'not_found', 'message': '설정된 로컬 경로에서 MR을 찾지 못했습니다.', 'scanned': scanned, 'roots': roots}


def _find_ytdlp(config):
    configured = str(config.get('YTDLP_PATH') or '').strip()
    if configured:
        if os.path.isfile(configured) and os.access(configured, os.X_OK):
            return configured
        found = shutil.which(configured)
        if found:
            return found
    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(plugin_dir, 'bin', 'yt-dlp'),
        os.path.join(plugin_dir, 'bin', 'yt-dlp_linux'),
    ]
    # 플러그인 교체 시 기존 bin/yt-dlp가 백업 폴더에 남아 있을 수 있다.
    # 이 경우 사용자가 다시 설치하지 않아도 기존 바이너리를 계속 사용할 수 있게 한다.
    parent = os.path.dirname(plugin_dir)
    try:
        for name in os.listdir(parent):
            if name.startswith(os.path.basename(plugin_dir) + '_') and 'backup' in name.lower():
                candidates.extend([
                    os.path.join(parent, name, 'bin', 'yt-dlp'),
                    os.path.join(parent, name, 'bin', 'yt-dlp_linux'),
                ])
    except OSError:
        pass
    candidates.extend(['/usr/local/bin/yt-dlp', '/usr/bin/yt-dlp', '/usr/local/bin/yt-dlp_linux', '/usr/bin/yt-dlp_linux'])
    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which('yt-dlp') or shutil.which('yt-dlp_linux')


def _run_ytdlp_search(binary, search_target, timeout, mode='json'):
    """검색 결과를 최대한 단순하게 받아온다.

    운영 환경의 yt-dlp 설정/버전에 따라 --print 동작이 달라질 수 있으므로
    1차는 flat-playlist + dump-json, 2차는 flat-playlist + print로 재시도한다.
    사용자 환경의 정상 동작을 방해할 수 있는 --ignore-config는 사용하지 않는다.
    """
    base = [binary, '--flat-playlist', '--skip-download', '--no-warnings']
    if mode == 'json':
        cmd = base + ['--dump-json', search_target]
    else:
        cmd = base + ['--print', '%(id)s\t%(title)s\t%(channel)s\t%(uploader)s', search_target]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def _parse_ytdlp_json(stdout):
    items = []
    for line in (stdout or '').splitlines():
        line = line.strip()
        if not line or not line.startswith('{'):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        vid = str(obj.get('id') or '').strip()
        title = str(obj.get('title') or '').strip()
        if not vid or not title:
            continue
        items.append({
            'id': vid,
            'title': title,
            'channel': str(obj.get('channel') or obj.get('uploader') or '').strip(),
        })
    return items


def _parse_ytdlp_print(stdout):
    items = []
    for line in (stdout or '').splitlines():
        parts = line.strip().split('\t', 3)
        if len(parts) < 2:
            continue
        vid, title = parts[0].strip(), parts[1].strip()
        channel = (parts[2] if len(parts) >= 3 else '') or (parts[3] if len(parts) >= 4 else '')
        if vid and title:
            items.append({'id': vid, 'title': title, 'channel': channel.strip()})
    return items


def _parse_ytdlp_lines(stdout):
    # --get-id/--get-title fallback용 단순 라인 파서
    items = []
    lines = [x.strip() for x in (stdout or '').splitlines() if x.strip()]
    for i in range(0, len(lines) - 1, 2):
        vid, title = lines[i], lines[i + 1]
        if re.fullmatch(r'[A-Za-z0-9_-]{6,20}', vid) and title:
            items.append({'id': vid, 'title': title, 'channel': ''})
    return items


def _ytdlp_search(song, config):
    """yt-dlp로 YouTube 검색 결과만 가져온다.

    실제 미디어 다운로드는 하지 않는다. JSON 출력이 파싱되지 않는
    환경에서는 print 출력으로 한 번 더 재시도한다.
    """
    binary = _find_ytdlp(config)
    if not binary:
        return [], {'status': 'unavailable', 'message': 'yt-dlp가 설치되어 있지 않습니다.'}

    title = str(song.get('title') or '').strip()
    singer = str(song.get('singer') or '').strip()
    brand = str(song.get('brand') or '').strip()
    number = str(song.get('no') or '').strip()
    brand_label = '금영' if brand == 'kumyoung' else 'TJ' if brand == 'tj' else brand
    queries = []
    # 검색어를 다양화해 여러 업로더/채널의 후보를 최대한 많이 모은다. 같은 채널의 영상은
    # 임베드 허용 여부가 보통 채널 단위로 동일해서(하나가 막히면 그 채널 영상은 대부분 막힘),
    # 후보 채널의 다양성을 늘리는 것이 실제 재생 성공률을 올리는 데 가장 효과적이다.
    for query in (
        ' '.join(x for x in (title, singer, 'MR', '노래방') if x),
        ' '.join(x for x in (title, singer, '금영', 'mr') if x),
        ' '.join(x for x in (singer, title, 'instrumental') if x),
        ' '.join(x for x in (brand, number, title, singer, 'MR') if x),
        ' '.join(x for x in (title, singer, '반주') if x),
        ' '.join(x for x in (title, singer, '가라오케') if x),
        ' '.join(x for x in (title, brand_label, 'mr') if x),
        ' '.join(x for x in (title, singer, 'karaoke instrumental') if x),
        title,
    ):
        query = query.strip()
        if query and query not in queries:
            queries.append(query)

    max_results = min(15, max(5, int(config.get('MR_YTDLP_RESULTS') or 10)))
    # 여러 채널을 확보하기 위한 목표치. 개별 쿼리의 max_results보다 넉넉하게 잡아,
    # 후보가 한 채널에 몰리지 않고 여러 쿼리에 걸쳐 다양하게 모이도록 한다.
    # yt-dlp 서브프로세스 1회 호출 자체가 (네트워크 검색 왕복 + 프로세스 기동 비용으로)
    # HTTP API 호출보다 훨씬 비싸다. 예전엔 20개(max_results*2)가 모일 때까지 최대 9개
    # 검색어를 순차로 다 태웠는데, 검색어 하나당 못해도 1~수 초가 걸려서 곡 하나 예약/
    # 다음곡 전환마다 20~30초씩 지연되거나 타임아웃으로 아예 실패하는 버그로 이어졌다
    # (실제 리포트됨). 보통 첫 1~2개 쿼리만으로도 채널 다양성 확보엔 충분하므로, 목표치를
    # max_results 자체(대략 절반)로 낮춰 조기 종료를 훨씬 앞당긴다.
    target_candidates = max_results
    # 기본값을 45초에서 15초로 낮춤 - json 모드가 실패하면 print/get-id-title 모드로
    # 각각 같은 타임아웃을 다시 쓰므로, 쿼리 하나가 최악의 경우 3배(구 135초)까지 걸릴
    # 수 있었다. 정상적인 검색은 보통 수 초 안에 끝나므로 15초면 충분하고, 그래도 느린
    # 환경이면 MR_SEARCH_TIMEOUT 설정으로 늘릴 수 있다.
    timeout = min(60, max(10, int(config.get('MR_SEARCH_TIMEOUT') or 15)))
    blocked_ids = queue_store.blocked_video_ids()
    all_items, seen, errors, query_stats = [], set(), [], []

    for query in queries:
        search_target = 'ytsearch%s:%s' % (max_results, query)
        started = time.time()
        parsed_items = []
        mode_used = 'json'
        try:
            proc = _run_ytdlp_search(binary, search_target, timeout, 'json')
            parsed_items = _parse_ytdlp_json(proc.stdout)
            stderr = (proc.stderr or '').strip()
            # returncode가 0이어도 출력이 비어 있는 경우가 있으므로 print 방식 재시도
            if not parsed_items:
                mode_used = 'print-fallback'
                proc2 = _run_ytdlp_search(binary, search_target, timeout, 'print')
                parsed_items = _parse_ytdlp_print(proc2.stdout)
                stderr2 = (proc2.stderr or '').strip()
                if not parsed_items:
                    mode_used = 'get-id-title-fallback'
                    cmd3 = [binary, '--flat-playlist', '--skip-download', '--no-warnings', '--get-id', '--get-title', search_target]
                    proc3 = subprocess.run(cmd3, capture_output=True, text=True, timeout=timeout, check=False)
                    parsed_items = _parse_ytdlp_lines(proc3.stdout)
                    stderr = stderr2 or stderr
                    if proc3.stderr:
                        stderr = proc3.stderr.strip() or stderr
                    returncode = proc3.returncode
                else:
                    stderr = stderr2 or stderr
                    returncode = proc2.returncode
            else:
                returncode = proc.returncode
        except subprocess.TimeoutExpired:
            errors.append('%s: 검색 시간 초과(%ss)' % (query, timeout))
            query_stats.append({'query': query, 'status': 'timeout', 'elapsed': round(time.time()-started,2)})
            continue
        except OSError as exc:
            errors.append('%s: %s' % (query, exc))
            query_stats.append({'query': query, 'status': 'error', 'message': str(exc), 'elapsed': round(time.time()-started,2)})
            continue

        accepted = 0
        for item in parsed_items:
            vid = item['id']
            if not re.fullmatch(r'[A-Za-z0-9_-]{6,20}', vid) or vid in seen:
                continue
            if vid in blocked_ids:
                continue
            seen.add(vid)
            item_title = item['title']
            channel = item.get('channel') or ''
            if not _candidate_is_allowed(item_title, channel):
                continue
            score = _score_title(title, singer, item_title + ' ' + channel, brand=brand, number=number)
            all_items.append({
                'id': vid,
                'title': item_title,
                'channel': channel,
                'score': score,
                'thumbnail': 'https://i.ytimg.com/vi/%s/mqdefault.jpg' % vid,
                'embed_url': _youtube_embed(vid),
                'watch_url': _youtube_watch(vid),
                'query': query,
            })
            accepted += 1

        stat = {
            'query': query,
            'status': 'ok' if returncode == 0 or accepted else 'error',
            'returncode': returncode,
            'results': accepted,
            'raw_results': len(parsed_items),
            'mode': mode_used,
            'elapsed': round(time.time()-started,2),
        }
        if stderr:
            stat['stderr'] = stderr[-800:]
        query_stats.append(stat)
        if returncode != 0 and not accepted:
            errors.append('%s: %s' % (query, stderr[-500:] if stderr else 'yt-dlp 검색 실패'))

        # 여러 채널에 걸쳐 충분한 후보가 확보되면 불필요한 추가 검색을 하지 않는다.
        if len(all_items) >= target_candidates:
            break

    all_items.sort(key=lambda x: (-int(x.get('score') or 0), x.get('title') or ''))
    # 채널 다양성 재정렬: 점수 순서를 최대한 지키되, 같은 채널 영상이 상위권에 연달아
    # 몰리지 않도록 라운드로빈으로 채널을 섞는다. 한 채널이 임베드를 막아두면 그 채널의
    # 다른 영상도 대부분 막혀 있으므로, 실패 시 넘어갈 다음 후보는 다른 채널일수록 좋다.
    by_channel = {}
    for item in all_items:
        by_channel.setdefault(item.get('channel') or '', []).append(item)
    diversified = []
    while len(diversified) < len(all_items):
        progressed = False
        for channel_items in by_channel.values():
            if channel_items:
                diversified.append(channel_items.pop(0))
                progressed = True
        if not progressed:
            break
    all_items = diversified[:max(max_results, 8)]
    info = {
        'status': 'ok' if all_items else 'error',
        'binary': binary,
        'queries': queries,
        'query_stats': query_stats,
        'candidate_count': len(all_items),
        'method': 'flat-playlist-dump-json-with-print-fallback',
    }
    if all_items:
        return all_items, info
    info['message'] = '; '.join(errors) or 'YouTube 검색 결과가 없습니다.'
    return [], info

def _api_search(song, config):
    key = str(config.get('YOUTUBE_API_KEY') or '').strip()
    title = str(song.get('title') or '').strip()
    singer = str(song.get('singer') or '').strip()
    brand = str(song.get('brand') or '').strip()
    number = str(song.get('no') or '').strip()
    brand_label = '금영' if brand == 'kumyoung' else 'TJ' if brand == 'tj' else brand
    default_query = ' '.join(x for x in (title, singer, 'MR', 'karaoke') if x).strip()
    watch_search = 'https://www.youtube.com/results?search_query=' + urllib.parse.quote(default_query)
    if not key:
        return [], {'status': 'disabled', 'message': 'YouTube API 키가 설정되지 않았습니다.', 'fallback_url': watch_search}
    # ytdlp 경로와 마찬가지로 검색어를 다양화해 여러 채널의 후보를 모은다.
    queries = []
    for query in (
        default_query,
        ' '.join(x for x in (title, singer, '금영', 'mr') if x),
        ' '.join(x for x in (singer, title, 'instrumental') if x),
        ' '.join(x for x in (brand, number, title, singer, 'MR') if x),
        ' '.join(x for x in (title, brand_label, 'mr') if x),
        ' '.join(x for x in (title, singer, '반주') if x),
    ):
        query = query.strip()
        if query and query not in queries:
            queries.append(query)

    blocked_ids = queue_store.blocked_video_ids()
    videos, seen = [], set()
    last_error = None
    for query in queries:
        params = {
            'part': 'snippet', 'type': 'video', 'maxResults': '10',
            'videoEmbeddable': 'true', 'q': query, 'key': key,
        }
        req = urllib.request.Request('%s?%s' % (YOUTUBE_SEARCH, urllib.parse.urlencode(params)), headers={'User-Agent': 'BookOasis-karaoke_singer/2.3.1'})
        try:
            with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode('utf-8', errors='replace'))
        except urllib.error.HTTPError as exc:
            last_error = 'YouTube API HTTP %s' % exc.code
            continue
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            continue
        for item in payload.get('items') or []:
            vid = str((item.get('id') or {}).get('videoId') or '').strip()
            snippet = item.get('snippet') or {}
            if not vid or vid in seen:
                continue
            if vid in blocked_ids:
                continue
            seen.add(vid)
            item_title = str(snippet.get('title') or '')
            channel = str(snippet.get('channelTitle') or '')
            if not _candidate_is_allowed(item_title, channel):
                continue
            videos.append({
                'id': vid,
                'title': item_title,
                'channel': channel,
                'score': _score_title(title, singer, item_title + ' ' + channel, brand=brand, number=number),
                'thumbnail': (((snippet.get('thumbnails') or {}).get('medium') or (snippet.get('thumbnails') or {}).get('default') or {}).get('url') or ''),
                'embed_url': _youtube_embed(vid),
                'watch_url': _youtube_watch(vid),
                'query': query,
            })
        # 원래 20개가 모일 때까지 최대 5개 쿼리(쿼리당 search.list 100유닛)를 전부 태웠는데,
        # 대부분의 경우 첫 쿼리 하나(최대 10개)로도 충분한 후보가 나온다. 곡 하나 예약할
        # 때마다 최악의 경우 500유닛까지 쓰면 기본 일일 쿼터(10,000)로 하루 20곡도 못
        # 채우고 429(쿼터 초과)에 걸린다 - 실제 리포트된 버그. 8개만 모여도 충분히 다양한
        # 후보로 보고 조기 종료해 쿼터 소모를 최대 5분의 1까지 줄인다.
        if len(videos) >= 8:
            break
    if not videos and last_error:
        return [], {'status': 'error', 'message': last_error, 'fallback_url': watch_search}
    videos.sort(key=lambda x: -int(x.get('score') or 0))
    # ytdlp 경로와 동일하게 채널 다양성을 위해 라운드로빈으로 섞는다.
    by_channel = {}
    for item in videos:
        by_channel.setdefault(item.get('channel') or '', []).append(item)
    diversified = []
    while len(diversified) < len(videos):
        progressed = False
        for channel_items in by_channel.values():
            if channel_items:
                diversified.append(channel_items.pop(0))
                progressed = True
        if not progressed:
            break
    return diversified[:15], {'status': 'ok', 'fallback_url': watch_search}


def extract_video_id(text):
    """유튜브 URL 또는 순수 video_id 문자열에서 11자리 video_id를 추출한다."""
    text = str(text or '').strip()
    if not text:
        return ''
    if re.fullmatch(r'[A-Za-z0-9_-]{6,20}', text) and 'http' not in text and '.' not in text:
        return text
    patterns = (
        r'(?:youtube\.com/watch\?[^#]*\bv=|youtube\.com/shorts/|youtu\.be/|youtube\.com/embed/)([A-Za-z0-9_-]{6,20})',
    )
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1)
    return ''


def set_manual(song, url_or_id, title=''):
    video_id = extract_video_id(url_or_id)
    if not video_id:
        return False, '유튜브 URL 또는 video_id를 인식하지 못했습니다.'
    key = _cache_key(song)
    queue_store.set_manual_mr(key, video_id, title=title or song.get('title') or '')
    queue_store.clear_mr_cache(key)
    return True, ''


def clear_manual(song):
    key = _cache_key(song)
    return queue_store.clear_manual_mr(key)


def resolve(song, config, force=False):
    song = dict(song or {})
    key = _cache_key(song)
    trace = []
    blocked = queue_store.blocked_video_ids()

    manual = queue_store.get_manual_mr(key)
    if manual and manual.get('video_id'):
        if manual['video_id'] not in blocked:
            vid = manual['video_id']
            result = _result(
                'manual', 'ok', video_id=vid, title=manual.get('title') or song.get('title') or '',
                channel='', score=999, thumbnail='', embed_url=_youtube_embed(vid),
                watch_url=_youtube_watch(vid), candidates=[],
            )
            trace.append({'step': 'manual', 'status': 'ok', 'message': '수동 지정된 MR을 사용합니다.'})
            return result, trace
        trace.append({'step': 'manual', 'status': 'blocked', 'message': '수동 지정된 영상이 차단 목록에 있어 자동 검색으로 전환합니다.'})

    if not force:
        cached = queue_store.get_mr_cache(key)
        if cached and cached.get('status') == 'ok' and cached.get('video_id') not in blocked and _candidate_is_allowed(cached.get('title'), cached.get('channel')) and _cache_matches_song(song, cached):
            # 캐시에 대체 후보(candidates)가 있다면 그 중 차단된 영상만 걸러서 함께 내려준다.
            cand = [c for c in (cached.get('candidates') or []) if c.get('id') not in blocked and _candidate_is_allowed(c.get('title'), c.get('channel'))]
            cached['candidates'] = cand
            trace.append({'step': 'cache', 'status': 'hit'})
            return cached, trace
        if cached and cached.get('video_id') in blocked:
            trace.append({'step': 'cache', 'status': 'stale-blocked', 'message': '캐시된 영상이 차단 목록에 있어 다시 검색합니다.'})
            queue_store.clear_mr_cache(key)
        elif cached and not _candidate_is_allowed(cached.get('title'), cached.get('channel')):
            trace.append({'step': 'cache', 'status': 'stale-filtered', 'message': '비MR 캐시를 폐기하고 다시 검색합니다.'})
            queue_store.clear_mr_cache(key)
        elif cached and not _cache_matches_song(song, cached):
            trace.append({'step': 'cache', 'status': 'stale-mismatch', 'message': '다른 곡에 연결된 오래된 캐시를 폐기하고 다시 검색합니다.'})
            queue_store.clear_mr_cache(key)
        else:
            trace.append({'step': 'cache', 'status': 'miss'})

    local, info = _local_search(song, config)
    trace.append({'step': 'local', **info})
    if local and local.get('media_url'):
        queue_store.save_mr_cache(key, local)
        return local, trace
    if local and not local.get('media_url'):
        trace.append({'step': 'local-playback', 'status': 'unavailable', 'message': '로컬 파일은 찾았지만 브라우저용 URL 매핑이 없어 YouTube 검색으로 전환합니다.'})

    videos, info = _ytdlp_search(song, config)
    trace.append({'step': 'yt-dlp', **info})
    if videos:
        best = videos[0]
        result = _result('youtube-ytdlp', 'ok', video_id=best['id'], title=best['title'], channel=best.get('channel',''), score=best.get('score',0), thumbnail=best.get('thumbnail',''), embed_url=best['embed_url'], watch_url=best['watch_url'], candidates=videos[:15])
        queue_store.save_mr_cache(key, result)
        return result, trace

    videos, info = _api_search(song, config)
    trace.append({'step': 'youtube-api', **info})
    if videos:
        best = videos[0]
        result = _result('youtube-api', 'ok', video_id=best['id'], title=best['title'], channel=best.get('channel',''), score=best.get('score',0), thumbnail=best.get('thumbnail',''), embed_url=best['embed_url'], watch_url=best['watch_url'], candidates=videos[:15])
        queue_store.save_mr_cache(key, result)
        return result, trace

    message = 'MR을 찾지 못했습니다. '
    if trace:
        last = trace[-1]
        message += str(last.get('message') or '')
    return _result('none', 'not_found', message=message), trace


def report_playback_error(song, video_id, config, reason='embed_disabled'):
    """재생 중 오류(임베드 차단 등)가 발생한 영상을 신고한다.

    - 해당 video_id를 전역 차단 목록에 추가해 이후 어떤 곡의 MR 검색에도
      다시 후보로 나오지 않도록 한다.
    - 이 곡의 캐시를 지우고 즉시 재검색해, 남은 후보/새 검색 결과 중
      차단되지 않은 영상으로 바로 이어서 재생할 수 있게 한다.
    """
    song = dict(song or {})
    video_id = str(video_id or '').strip()
    if video_id:
        queue_store.block_video(video_id, reason=reason)
    key = _cache_key(song)
    queue_store.clear_mr_cache(key)
    return resolve(song, config, force=True)


def diagnostics(config):
    binary = _find_ytdlp(config)
    version = ''
    if binary:
        try:
            p = subprocess.run([binary, '--version'], capture_output=True, text=True, timeout=5, check=False)
            version = (p.stdout or p.stderr or '').strip()
        except Exception as exc:
            version = str(exc)
    return {
        'yt_dlp': bool(binary),
        'yt_dlp_path': binary or '',
        'yt_dlp_version': version,
        'youtube_api': bool(str(config.get('YOUTUBE_API_KEY') or '').strip()),
        'local_paths': _local_roots(config),
        'local_public_maps': str(config.get('MR_LOCAL_PUBLIC_MAPS') or '').strip(),
    }
