"""SQLite storage. One short-lived connection per operation; works across worker threads."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


def segment_dict(row):
    result = dict(row)
    result['translation_skipped'] = bool(result['translation_skipped'])
    return result


class Store:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'meetings.sqlite3'
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS meetings (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
                    ended_at TEXT, status TEXT NOT NULL, source TEXT NOT NULL,
                    target TEXT NOT NULL, glossary TEXT NOT NULL, provider TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    meeting_id TEXT NOT NULL REFERENCES meetings(id), text TEXT NOT NULL,
                    translation TEXT NOT NULL DEFAULT '', start_ms INTEGER NOT NULL,
                    end_ms INTEGER NOT NULL, asr_ms REAL, translation_ms REAL,
                    audio_path TEXT, error TEXT, created_at TEXT NOT NULL,
                    translation_skipped INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    meeting_id TEXT NOT NULL REFERENCES meetings(id), content TEXT NOT NULL,
                    segment_ids TEXT NOT NULL, latency_ms REAL NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS segments_meeting ON segments(meeting_id, id);
                CREATE INDEX IF NOT EXISTS summaries_meeting ON summaries(meeting_id, id);
            ''')
            if 'translation_skipped' not in {row['name'] for row in db.execute('PRAGMA table_info(segments)')}:
                db.execute('ALTER TABLE segments ADD COLUMN translation_skipped INTEGER NOT NULL DEFAULT 0')
            # A stopped process cannot retain an actively recording meeting.
            db.execute("UPDATE meetings SET status='interrupted' WHERE status IN ('live','finishing')")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, title, source, target, glossary, provider):
        mid = uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO meetings (id,title,created_at,status,source,target,glossary,provider) VALUES (?,?,?,?,?,?,?,?)',
                       (mid, title, now(), 'live', source, target, glossary, provider))
        return self.get(mid)

    def list(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('''
                SELECT m.*, (SELECT count(*) FROM segments s WHERE s.meeting_id=m.id) segment_count
                FROM meetings m ORDER BY created_at DESC LIMIT 100
            ''')]

    def get(self, mid):
        with self.connect() as db:
            row = db.execute('SELECT * FROM meetings WHERE id=?', (mid,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result['segments'] = [segment_dict(r) for r in db.execute(
                'SELECT * FROM segments WHERE meeting_id=? ORDER BY id', (mid,))]
            result['summaries'] = []
            for r in db.execute('SELECT * FROM summaries WHERE meeting_id=? ORDER BY id', (mid,)):
                item = dict(r)
                item['segment_ids'] = json.loads(item['segment_ids'])
                result['summaries'].append(item)
            return result

    def segment(self, mid, text, start_ms, end_ms, asr_ms=None, audio_path=None, skip_translation=False):
        with self.connect() as db:
            cur = db.execute('''INSERT INTO segments
                (meeting_id,text,start_ms,end_ms,asr_ms,audio_path,created_at,translation_skipped,translation_ms)
                VALUES (?,?,?,?,?,?,?,?,?)''',
                (mid, text, start_ms, end_ms, asr_ms, audio_path, now(),
                 int(skip_translation), 0 if skip_translation else None))
            return segment_dict(db.execute('SELECT * FROM segments WHERE id=?', (cur.lastrowid,)).fetchone())

    def translate(self, sid, text, latency_ms, error=None, skipped=False):
        with self.connect() as db:
            db.execute('UPDATE segments SET translation=?,translation_ms=?,error=?,translation_skipped=? WHERE id=?',
                       (text, latency_ms, error, int(skipped), sid))
            return segment_dict(db.execute('SELECT * FROM segments WHERE id=?', (sid,)).fetchone())

    def summary(self, mid, content, ids, latency_ms):
        with self.connect() as db:
            cur = db.execute('INSERT INTO summaries (meeting_id,content,segment_ids,latency_ms,created_at) VALUES (?,?,?,?,?)',
                             (mid, content, json.dumps(ids), latency_ms, now()))
            result = dict(db.execute('SELECT * FROM summaries WHERE id=?', (cur.lastrowid,)).fetchone())
            result['segment_ids'] = ids
            return result

    def status(self, mid, status):
        with self.connect() as db:
            db.execute('UPDATE meetings SET status=?, ended_at=? WHERE id=?',
                       (status, now() if status == 'completed' else None, mid))
