import sqlite3

from fastapi.testclient import TestClient
import pytest

from app.language import is_chinese_text
from app.main import create_app
from app.store import Store
from tests.test_app import FakeQwen, create, settings, until
from tests.test_live import packet
from uuid import uuid4


@pytest.mark.parametrize(('text', 'chinese'), [
    ('你好。', True), ('好', True), ('今天使用 AMD GPU 测试语音识别。', True),
    ('支持 Qwen3-ASR 0.6B。', True), ('繁體中文也保留原文。', True),
    ('我使用 Hugging Face。', True),
    ('Hello, welcome to this meeting.', False),
    ('We use 中文 terms in this model.', False), ('ROCm 7.13', False),
])
def test_actual_text_language_with_technical_terms(text, chinese):
    assert is_chinese_text(text) is chinese


def test_meeting_defaults_and_chinese_text_bypasses_translation(tmp_path):
    class Provider(FakeQwen):
        calls = []
        def translate(self, text, target, context, glossary):
            self.calls.append((text, target, glossary))
            return '欢迎参加会议。'

    provider = Provider()
    with TestClient(create_app(settings(tmp_path, provider='qwen'), provider)) as client:
        meeting = client.post('/api/meetings', json={}).json()
        assert (meeting['title'], meeting['source'], meeting['target'], meeting['glossary']) == ('新会议', 'en', 'zh', '')
        mid = meeting['id']
        with client.websocket_connect(f'/ws/{mid}') as ws:
            ws.receive_json()
            ws.send_json({'type': 'text', 'text': '今天使用 AMD GPU 测试。'})
            ws.send_json({'type': 'text', 'text': 'Welcome to the meeting.'})
            ws.send_json({'type': 'finish'})
            until(ws, lambda e: e['type'] == 'finished')
        meeting = client.get(f'/api/meetings/{mid}').json()
        zh, en = meeting['segments']
        assert zh['text'] == '今天使用 AMD GPU 测试。'
        assert zh['translation_skipped'] is True and zh['translation'] == '' and zh['error'] is None
        assert en['translation_skipped'] is False and en['translation'] == '欢迎参加会议。'
        assert provider.calls == [('Welcome to the meeting.', 'zh', '')]
        assert '（翻译未完成）' not in client.get(f'/api/meetings/{mid}/export?format=md').text
        assert meeting['summaries']
        with client.websocket_connect(f'/ws/{mid}') as ws:
            ws.receive_json(); ws.send_json({'type': 'retranslate'})
            until(ws, lambda e: e['type'] == 'retranslation_done')
        assert len(provider.calls) == 2  # Only the English sentence was retried.


def test_default_recording_detects_chinese_in_both_preview_and_final(tmp_path):
    class Chinese(FakeQwen):
        sources = []
        def transcribe(self, path, source, glossary):
            self.sources.append((source, glossary))
            return '这是中文录音。'
        def translate(self, *args):
            raise AssertionError('Chinese must never reach the translation model')

    provider = Chinese()
    with TestClient(create_app(settings(tmp_path, provider='qwen'), provider)) as client:
        mid, uid = create(client), uuid4().hex
        with client.websocket_connect(f'/ws/{mid}') as ws:
            ws.receive_json(); ws.send_json(packet(uid, 1))
            until(ws, lambda e: e['type'] == 'transcript_partial')
            ws.send_json(packet(uid, 2, final=True))
            until(ws, lambda e: e['type'] == 'audio_done')
            ws.send_json({'type': 'finish'})
            until(ws, lambda e: e['type'] == 'finished')
        assert provider.sources == [('auto', ''), ('auto', '')]
        assert client.get(f'/api/meetings/{mid}').json()['segments'][0]['translation_skipped'] is True


def test_legacy_database_keeps_existing_rows_when_skip_flag_is_added(tmp_path):
    with sqlite3.connect(tmp_path / 'meetings.sqlite3') as db:
        db.executescript('''
          CREATE TABLE meetings (id TEXT PRIMARY KEY, title TEXT, created_at TEXT,
            ended_at TEXT, status TEXT, source TEXT, target TEXT, glossary TEXT, provider TEXT);
          CREATE TABLE segments (id INTEGER PRIMARY KEY, meeting_id TEXT, text TEXT,
            translation TEXT, start_ms INTEGER, end_ms INTEGER, asr_ms REAL,
            translation_ms REAL, audio_path TEXT, error TEXT, created_at TEXT);
          INSERT INTO meetings VALUES ('old','Old','2026-10-24',NULL,'completed','en','zh','','qwen');
          INSERT INTO segments VALUES (7,'old','Source.','原来的译文。',0,1000,5,10,'audio/old.wav',NULL,'2026-10-24');
        ''')
    store = Store(tmp_path)
    segment = store.get('old')['segments'][0]
    assert segment['id'] == 7 and segment['translation'] == '原来的译文。'
    assert segment['audio_path'] == 'audio/old.wav' and segment['translation_skipped'] is False
    Store(tmp_path)  # Migration is safe to run again.
