# -*- coding: utf-8 -*-
"""노래방 예약/재생/순환 상태 저장소.

- 예약 대기열
- 현재 재생 상태
- 재생 이력
- 예약자별 Fair Rotation
을 표준 SQLite로 관리합니다.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

_LOCK = threading.RLock()


def _db_path():
    from .persistent import data_dir
    return os.path.join(data_dir(), 'karaoke_queue.sqlite3')


def _connect():
    conn = sqlite3.connect(_db_path(), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL 대신 기본(DELETE) 저널 모드를 쓴다. 이 앱은 모든 DB 접근을 자체 _LOCK으로
    # 직렬화하고 있어 WAL의 동시 읽기 이점이 필요 없고, WAL이 만드는 -wal/-shm 부속
    # 파일이 생겼다 사라졌다 하면서 플러그인 zip 업데이트의 백업 단계가 "파일이 갑자기
    # 없어졌다"며 실패하고 롤백되는 문제가 있었다. DELETE 모드는 그런 부속 파일을 남기지
    # 않아 업데이트 백업이 항상 안정적으로 동작한다.
    conn.execute('PRAGMA journal_mode=DELETE')
    conn.execute('PRAGMA busy_timeout=15000')
    return conn


def init_db():
    with _LOCK:
        conn = _connect()
        try:
            conn.executescript('''
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                song_id TEXT NOT NULL,
                brand TEXT,
                no TEXT,
                title TEXT NOT NULL,
                singer TEXT,
                requester TEXT,
                media_type TEXT DEFAULT '',
                media_url TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'waiting',
                queue_order INTEGER NOT NULL DEFAULT 0,
                requested_at REAL NOT NULL,
                started_at REAL,
                finished_at REAL,
                error TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_queue_status_order ON queue(status, queue_order, id);
            CREATE INDEX IF NOT EXISTS idx_queue_song_requester ON queue(song_id, requester, status);
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                queue_id INTEGER,
                song_id TEXT NOT NULL,
                brand TEXT,
                no TEXT,
                title TEXT NOT NULL,
                singer TEXT,
                requester TEXT,
                status TEXT NOT NULL,
                started_at REAL,
                finished_at REAL NOT NULL,
                error TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_history_finished ON history(finished_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_history_requester ON history(requester, finished_at DESC);
            CREATE TABLE IF NOT EXISTS player_state (
                id INTEGER PRIMARY KEY CHECK(id=1),
                queue_id INTEGER,
                state TEXT NOT NULL DEFAULT 'idle',
                updated_at REAL NOT NULL,
                error TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS mr_cache (
                song_key TEXT PRIMARY KEY,
                source TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'ok',
                video_id TEXT DEFAULT '',
                title TEXT DEFAULT '',
                channel TEXT DEFAULT '',
                media_type TEXT DEFAULT '',
                media_url TEXT DEFAULT '',
                path TEXT DEFAULT '',
                embed_url TEXT DEFAULT '',
                watch_url TEXT DEFAULT '',
                thumbnail TEXT DEFAULT '',
                score REAL DEFAULT 0,
                checked_at REAL NOT NULL,
                fail_count INTEGER NOT NULL DEFAULT 0,
                error TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_mr_cache_checked ON mr_cache(checked_at DESC);
            CREATE TABLE IF NOT EXISTS blocked_videos (
                video_id TEXT PRIMARY KEY,
                reason TEXT DEFAULT '',
                blocked_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS manual_mr (
                song_key TEXT PRIMARY KEY,
                video_id TEXT NOT NULL,
                title TEXT DEFAULT '',
                set_at REAL NOT NULL
            );
            ''')
            # mr_cache에 candidates 컬럼이 없는 기존 DB(업그레이드 전 생성분)를 위한 마이그레이션.
            # 이 컬럼이 없으면 캐시 재생 시 대체 후보가 없어, 임베드가 차단된 영상이 캐시되어
            # 있을 경우 "동영상을 재생할 수 없음" 화면에서 자동으로 다음 후보로 넘어가지 못한다.
            existing_cols = {r['name'] for r in conn.execute('PRAGMA table_info(mr_cache)').fetchall()}
            if 'candidates' not in existing_cols:
                conn.execute("ALTER TABLE mr_cache ADD COLUMN candidates TEXT DEFAULT ''")
            conn.execute("INSERT OR IGNORE INTO player_state(id,state,updated_at) VALUES(1,'idle',?)", (time.time(),))
            conn.commit()
        finally:
            conn.close()


def _row(row):
    return dict(row) if row else None


def _normalize_requester(value):
    return str(value or '').strip()[:100]


def add_song(song, requester=''):
    init_db()
    now = time.time()
    song_id = str(song.get('id') or '%s:%s' % (song.get('brand') or '', song.get('no') or '')).strip()
    title = str(song.get('title') or '').strip()
    if not title:
        raise ValueError('곡 제목이 없습니다.')
    requester = _normalize_requester(requester)
    with _LOCK:
        conn = _connect()
        try:
            dup = conn.execute('''SELECT id FROM queue
                WHERE status='waiting' AND song_id=? AND COALESCE(requester,'')=?
                LIMIT 1''', (song_id, requester)).fetchone()
            if dup:
                return {'duplicate': True, 'id': dup['id']}
            pos = conn.execute("SELECT COALESCE(MAX(queue_order),0)+1 AS n FROM queue WHERE status='waiting'").fetchone()['n']
            cur = conn.execute('''INSERT INTO queue
                (song_id,brand,no,title,singer,requester,media_type,media_url,status,queue_order,requested_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (
                song_id, song.get('brand') or '', song.get('no') or '', title,
                song.get('singer') or '', requester, song.get('media_type') or '',
                song.get('media_url') or '', 'waiting', int(pos), now))
            conn.commit()
            return {'duplicate': False, 'id': cur.lastrowid}
        finally:
            conn.close()


def _last_requester(conn):
    row = conn.execute('''SELECT requester FROM history
                          WHERE status IN ('played','skipped')
                          ORDER BY finished_at DESC,id DESC LIMIT 1''').fetchone()
    return _normalize_requester(row['requester']) if row else ''


def _choose_fair_candidate(conn):
    rows = conn.execute("SELECT * FROM queue WHERE status='waiting' ORDER BY queue_order,id").fetchall()
    if not rows:
        return None

    # 예약자가 여러 명이면 직전 예약자를 한 번 쉬게 하는 것이 1차 Fair Rotation입니다.
    requesters = []
    for r in rows:
        key = _normalize_requester(r['requester']) or '__anonymous_%s' % r['id']
        if key not in requesters:
            requesters.append(key)

    last = _last_requester(conn)
    if len(requesters) > 1 and last:
        eligible = [r for r in rows if (_normalize_requester(r['requester']) or '__anonymous_%s' % r['id']) != last]
        if eligible:
            rows = eligible

    # 같은 예약자 안에서는 가장 오래 전에 재생된 예약자를 우선합니다.
    scored = []
    for r in rows:
        requester = _normalize_requester(r['requester'])
        if requester:
            hist = conn.execute('''SELECT finished_at FROM history
                                   WHERE requester=? AND status IN ('played','skipped')
                                   ORDER BY finished_at DESC,id DESC LIMIT 1''', (requester,)).fetchone()
            last_played = float(hist['finished_at']) if hist else 0.0
        else:
            last_played = 0.0
        scored.append((last_played, int(r['queue_order'] or 0), int(r['id']), r))
    scored.sort(key=lambda x: (x[0], x[1], x[2]))
    return scored[0][3]


def list_queue(limit=100):
    init_db()
    limit = min(500, max(1, int(limit or 100)))
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute('''SELECT * FROM queue
                WHERE status IN ('waiting','playing','paused','error')
                ORDER BY CASE status WHEN 'playing' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END,
                         queue_order,id LIMIT ?''', (limit,)).fetchall()
            result = [_row(r) for r in rows]
            current = conn.execute("SELECT * FROM queue WHERE status IN ('playing','paused') ORDER BY started_at DESC,id DESC LIMIT 1").fetchone()
            return {'queue': result, 'current': _row(current), 'last_requester': _last_requester(conn)}
        finally:
            conn.close()


def _renumber(conn):
    rows = conn.execute("SELECT id FROM queue WHERE status='waiting' ORDER BY queue_order,id").fetchall()
    for i, row in enumerate(rows, 1):
        conn.execute('UPDATE queue SET queue_order=? WHERE id=?', (i, row['id']))


def has_active_current():
    """현재 재생/일시정지 중인 곡이 이미 있는지 부작용 없이 확인합니다.

    예약(queue_add) 시 '방금 이 예약으로 재생이 새로 시작됐는지'(autostarted)를 정확히
    판정하려면, start_next() 호출 *전*에 이미 재생 중이던 곡이 있었는지를 알아야 합니다.
    start_next() 자체는 이미 재생 중이면 그 곡을 그대로 반환할 뿐 아무것도 바꾸지
    않지만, 호출부가 반환값의 유무만으로 '새로 시작됐다'고 잘못 판단하면 이미 재생 중인
    곡을 다시 재생 요청하는 버그로 이어진다.
    """
    init_db()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM queue WHERE status IN ('playing','paused') LIMIT 1"
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def start_next():
    """현재 재생이 없으면 Fair Rotation 규칙으로 다음 예약곡을 시작합니다.
    """
    init_db()
    now = time.time()
    with _LOCK:
        conn = _connect()
        try:
            current = conn.execute("SELECT * FROM queue WHERE status IN ('playing','paused') ORDER BY started_at DESC,id DESC LIMIT 1").fetchone()
            if current:
                return _row(current)
            row = _choose_fair_candidate(conn)
            if not row:
                conn.execute("UPDATE player_state SET queue_id=NULL,state='idle',updated_at=?,error='' WHERE id=1", (now,))
                conn.commit()
                return None
            conn.execute("UPDATE queue SET status='playing',started_at=?,error='' WHERE id=?", (now,row['id']))
            conn.execute("UPDATE player_state SET queue_id=?,state='playing',updated_at=?,error='' WHERE id=1", (row['id'],now))
            conn.commit()
            return _row(conn.execute('SELECT * FROM queue WHERE id=?', (row['id'],)).fetchone())
        finally:
            conn.close()


def finish(queue_id, status='played', error=''):
    init_db()
    now = time.time()
    status = status if status in ('played','skipped','cancelled','error') else 'played'
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT * FROM queue WHERE id=? AND status IN ('playing','paused')",
                (int(queue_id),)
            ).fetchone()
            if not row:
                return None
            conn.execute(
                'UPDATE queue SET status=?,finished_at=?,error=? WHERE id=? AND status IN (\'playing\',\'paused\')',
                (status, now, str(error or ''), int(queue_id))
            )
            conn.execute('''INSERT INTO history(queue_id,song_id,brand,no,title,singer,requester,status,started_at,finished_at,error)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)''', (row['id'],row['song_id'],row['brand'],row['no'],row['title'],row['singer'],row['requester'],status,row['started_at'],now,str(error or '')))
            conn.execute("UPDATE player_state SET queue_id=NULL,state=?,updated_at=?,error='' WHERE id=1", ('error' if status=='error' else 'ended',now))
            _renumber(conn)
            conn.commit()
            return _row(row)
        finally:
            conn.close()


def play_now(queue_id):
    """대기열에서 대기(waiting) 중인 특정 곡을 Fair Rotation 순서를 건너뛰고 즉시 재생으로
    전환한다. 현재 재생/일시정지 중이던 곡이 있으면(요청한 곡이 아닌 한) 'skipped'로 먼저
    마감한다 - finish()와 동일한 히스토리 기록 경로를 그대로 타므로 별도 분기 없이 재사용.
    """
    init_db()
    now = time.time()
    with _LOCK:
        conn = _connect()
        try:
            target = conn.execute(
                "SELECT * FROM queue WHERE id=? AND status='waiting'", (int(queue_id),)
            ).fetchone()
            if not target:
                return None
            current = conn.execute(
                "SELECT * FROM queue WHERE status IN ('playing','paused') LIMIT 1"
            ).fetchone()
        finally:
            conn.close()

    if current and current['id'] != int(queue_id):
        finish(current['id'], status='skipped')

    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM queue WHERE id=? AND status='waiting'", (int(queue_id),)).fetchone()
            if not row:
                return None
            conn.execute("UPDATE queue SET status='playing',started_at=?,error='' WHERE id=?", (now, row['id']))
            conn.execute("UPDATE player_state SET queue_id=?,state='playing',updated_at=?,error='' WHERE id=1", (row['id'], now))
            _renumber(conn)
            conn.commit()
            return _row(conn.execute('SELECT * FROM queue WHERE id=?', (row['id'],)).fetchone())
        finally:
            conn.close()


def remove(queue_id):
    init_db()
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute("SELECT id FROM queue WHERE id=? AND status='waiting'", (int(queue_id),)).fetchone()
            if not row:
                return False
            conn.execute('DELETE FROM queue WHERE id=?', (int(queue_id),))
            _renumber(conn)
            conn.commit()
            return True
        finally:
            conn.close()


def clear_waiting():
    init_db()
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM queue WHERE status='waiting'")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


def set_player_state(state, queue_id=None, error=''):
    init_db()
    allowed = {'idle','loading','playing','paused','ended','error'}
    state = state if state in allowed else 'error'
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("UPDATE player_state SET queue_id=?,state=?,updated_at=?,error=? WHERE id=1", (queue_id,state,time.time(),str(error or '')))
            conn.commit()
        finally:
            conn.close()


def get_player_state():
    init_db()
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute('SELECT * FROM player_state WHERE id=1').fetchone()
            return _row(row) or {'id':1,'queue_id':None,'state':'idle','updated_at':time.time(),'error':''}
        finally:
            conn.close()


def queue_summary():
    """대기열/현재곡/예약자별 대기 수를 한 번에 반환합니다."""
    init_db()
    with _LOCK:
        conn = _connect()
        try:
            waiting = conn.execute("SELECT COUNT(*) AS n FROM queue WHERE status='waiting'").fetchone()['n']
            current = conn.execute("SELECT * FROM queue WHERE status IN ('playing','paused') ORDER BY started_at DESC,id DESC LIMIT 1").fetchone()
            requesters = conn.execute("""SELECT requester, COUNT(*) AS waiting_count
                FROM queue WHERE status='waiting' AND TRIM(COALESCE(requester,''))<>''
                GROUP BY requester ORDER BY waiting_count DESC, requester COLLATE NOCASE""").fetchall()
            return {
                'waiting': int(waiting or 0),
                'current': _row(current),
                'requesters': [_row(r) for r in requesters],
            }
        finally:
            conn.close()


def get_mr_cache(song_key):
    init_db()
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute('SELECT * FROM mr_cache WHERE song_key=? LIMIT 1', (str(song_key),)).fetchone()
            data = _row(row)
            if data and data.get('candidates'):
                try:
                    data['candidates'] = json.loads(data['candidates'])
                except (TypeError, ValueError):
                    data['candidates'] = []
            elif data:
                data['candidates'] = []
            return data
        finally:
            conn.close()


def save_mr_cache(song_key, data):
    init_db()
    data = dict(data or {})
    now = time.time()
    candidates_json = json.dumps(data.get('candidates') or [], ensure_ascii=False)
    with _LOCK:
        conn = _connect()
        try:
            sql = """INSERT INTO mr_cache(song_key,source,status,video_id,title,channel,media_type,media_url,path,embed_url,watch_url,thumbnail,score,checked_at,fail_count,error,candidates)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(song_key) DO UPDATE SET source=excluded.source,status=excluded.status,video_id=excluded.video_id,title=excluded.title,channel=excluded.channel,media_type=excluded.media_type,media_url=excluded.media_url,path=excluded.path,embed_url=excluded.embed_url,watch_url=excluded.watch_url,thumbnail=excluded.thumbnail,score=excluded.score,checked_at=excluded.checked_at,fail_count=excluded.fail_count,error=excluded.error,candidates=excluded.candidates"""
            conn.execute(sql, (
                str(song_key), str(data.get('source') or ''), str(data.get('status') or 'ok'), str(data.get('video_id') or ''),
                str(data.get('title') or ''), str(data.get('channel') or ''), str(data.get('media_type') or ''), str(data.get('media_url') or ''),
                str(data.get('path') or ''), str(data.get('embed_url') or ''), str(data.get('watch_url') or ''), str(data.get('thumbnail') or ''),
                float(data.get('score') or 0), now, int(data.get('fail_count') or 0), str(data.get('error') or ''), candidates_json))
            conn.commit()
        finally:
            conn.close()


def block_video(video_id, reason=''):
    """특정 YouTube 영상을 향후 MR 후보에서 제외한다.

    (예: 소유자가 임베드 재생을 차단한 영상 - '동영상을 재생할 수 없음' 오류)
    """
    video_id = str(video_id or '').strip()
    if not video_id:
        return False
    init_db()
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                """INSERT INTO blocked_videos(video_id,reason,blocked_at) VALUES(?,?,?)
                   ON CONFLICT(video_id) DO UPDATE SET reason=excluded.reason,blocked_at=excluded.blocked_at""",
                (video_id, str(reason or ''), time.time())
            )
            conn.commit()
            return True
        finally:
            conn.close()


def unblock_video(video_id):
    init_db()
    video_id = str(video_id or '').strip()
    if not video_id:
        return False
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute('DELETE FROM blocked_videos WHERE video_id=?', (video_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def blocked_video_ids():
    init_db()
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute('SELECT video_id FROM blocked_videos').fetchall()
            return {r['video_id'] for r in rows}
        finally:
            conn.close()


def set_manual_mr(song_key, video_id, title=''):
    """특정 곡에 항상 사용할 유튜브 영상을 수동으로 지정한다(자동 검색보다 우선).

    같은 곡을 재생할 때마다 매번 이 영상을 사용하며, 자동 재검색으로 덮어써지지 않는다
    (관리자가 명시적으로 해제하기 전까지 유지).
    """
    init_db()
    song_key = str(song_key or '').strip()
    video_id = str(video_id or '').strip()
    if not song_key or not video_id:
        return False
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                """INSERT INTO manual_mr(song_key,video_id,title,set_at) VALUES(?,?,?,?)
                   ON CONFLICT(song_key) DO UPDATE SET video_id=excluded.video_id,title=excluded.title,set_at=excluded.set_at""",
                (song_key, video_id, str(title or ''), time.time())
            )
            conn.commit()
            return True
        finally:
            conn.close()


def get_manual_mr(song_key):
    init_db()
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute('SELECT * FROM manual_mr WHERE song_key=? LIMIT 1', (str(song_key),)).fetchone()
            return _row(row)
        finally:
            conn.close()


def clear_manual_mr(song_key):
    init_db()
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute('DELETE FROM manual_mr WHERE song_key=?', (str(song_key),))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def clear_mr_cache(song_key=None):
    init_db()
    with _LOCK:
        conn = _connect()
        try:
            if song_key:
                cur = conn.execute('DELETE FROM mr_cache WHERE song_key=?', (str(song_key),))
            else:
                cur = conn.execute('DELETE FROM mr_cache')
            conn.commit()
            return int(cur.rowcount or 0)
        finally:
            conn.close()


def log_preview(song, requester=''):
    """예약 없이 'MR' 버튼으로 바로 미리듣기한 곡도 재생 기록에 남긴다.

    기존에는 예약(대기열)을 거쳐 재생된 곡만 history 테이블에 기록되어, 미리듣기로만
    들은 곡은 '최근 재생 기록'에 전혀 나타나지 않았다.
    """
    init_db()
    song = dict(song or {})
    now = time.time()
    with _LOCK:
        conn = _connect()
        try:
            conn.execute('''INSERT INTO history(queue_id,song_id,brand,no,title,singer,requester,status,started_at,finished_at,error)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)''', (
                None,
                str(song.get('id') or ''),
                str(song.get('brand') or ''),
                str(song.get('no') or ''),
                str(song.get('title') or ''),
                str(song.get('singer') or ''),
                str(requester or ''),
                'preview',
                now, now, '',
            ))
            conn.commit()
        finally:
            conn.close()


def popular_songs(limit=20):
    """재생 기록(history)을 집계해 많이 재생된 순으로 곡을 반환한다(인기차트).

    예약 재생/미리듣기(preview) 모두 관심 신호로 보고 함께 집계한다.
    """
    init_db()
    limit = min(100, max(1, int(limit or 20)))
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                '''SELECT brand, no, title, singer, COUNT(*) AS play_count
                   FROM history
                   WHERE title != ''
                   GROUP BY brand, no
                   ORDER BY play_count DESC, MAX(finished_at) DESC
                   LIMIT ?''',
                (limit,)
            ).fetchall()
            return [_row(r) for r in rows]
        finally:
            conn.close()


def history(limit=50):
    init_db()
    limit = min(200, max(1, int(limit or 50)))
    with _LOCK:
        conn = _connect()
        try:
            return [_row(r) for r in conn.execute('SELECT * FROM history ORDER BY finished_at DESC,id DESC LIMIT ?', (limit,)).fetchall()]
        finally:
            conn.close()
