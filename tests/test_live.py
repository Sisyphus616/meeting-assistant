import base64
import threading
import time
from uuid import uuid4
import wave

from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.main import create_app
from app.sentences import split_sentences
from tests.test_app import FakeQwen, create, until


def packet(utterance, seconds, final=False):
    return {'type': 'audio' if final else 'audio_preview', 'utterance_id': utterance,
            'pcm16': base64.b64encode(b'\x00\x08' * (16000 * seconds)).decode(),
            'sample_rate': 16000, 'start_ms': 1000}


class Incremental(FakeQwen):
    def __init__(self):
        self.calls, self.translations = [], []

    def transcribe(self, path, source, glossary):
        with wave.open(str(path)) as audio:
            samples = audio.getnframes()
        self.calls.append(samples)
        return '今天讨论实时转写' if samples == 16000 else '今天讨论实时转写。然后测试自动断句！'

    def translate(self, text, *args):
        self.translations.append(text)
        return 'Translated: ' + text


def test_live_updates_before_final_and_reuses_last_decode(tmp_path):
    provider = Incremental()
    with TestClient(create_app(Settings(data_dir=tmp_path, provider='qwen'), provider)) as client:
        assert client.post('/api/asr/prepare').json()['ready']
        mid, uid = create(client), uuid4().hex
        with client.websocket_connect(f'/ws/{mid}') as ws:
            ws.receive_json()
            ws.send_json(packet(uid, 1))
            assert until(ws, lambda e: e['type'] == 'transcript_partial')['text'] == '今天讨论实时转写'
            assert client.get(f'/api/meetings/{mid}').json()['segments'] == []
            assert provider.translations == []
            ws.send_json(packet(uid, 2))
            assert '然后' in until(ws, lambda e: e['type'] == 'transcript_partial')['text']
            ws.send_json(packet(uid, 2, final=True))
            until(ws, lambda e: e['type'] == 'audio_done')
            ws.send_json({'type': 'finish'})
            until(ws, lambda e: e['type'] == 'finished')
        meeting = client.get(f'/api/meetings/{mid}').json()
        assert [s['text'] for s in meeting['segments']] == ['今天讨论实时转写。', '然后测试自动断句！']
        assert provider.calls == [16000, 32000]
        assert provider.translations == []
        assert all(s['translation_skipped'] for s in meeting['segments'])
        assert len({s['audio_path'] for s in meeting['segments']}) == 1
        assert client.get(f'/api/meetings/{mid}/export?format=srt').text.count(' --> ') == 1
        assert not list((tmp_path / 'previews').rglob('*.wav'))


def test_coalesces_outdated_previews_and_suppresses_stale_results(tmp_path):
    started, release = threading.Event(), threading.Event()

    class Slow(Incremental):
        def transcribe(self, *args):
            result = super().transcribe(*args)
            if len(self.calls) == 1:
                started.set()
                assert release.wait(5)
            return result

    provider = Slow()
    with TestClient(create_app(Settings(data_dir=tmp_path, provider='qwen'), provider)) as client:
        mid, uid = create(client), uuid4().hex
        with client.websocket_connect(f'/ws/{mid}') as ws:
            ws.receive_json(); ws.send_json(packet(uid, 1))
            assert started.wait(3)
            try:
                ws.send_json(packet(uid, 2)); ws.send_json(packet(uid, 3))
                ws.send_json(packet(uid, 3, final=True))
                ws.send_json({'type': 'ping'})
                until(ws, lambda e: e['type'] == 'metrics')  # Ordered receive barrier.
            finally:
                release.set()
            ws.send_json({'type': 'finish'})
            events = []
            while True:
                event = ws.receive_json(); events.append(event)
                if event['type'] == 'finished':
                    break
            assert not any(e['type'] == 'transcript_partial' for e in events)
            assert provider.calls == [16000, 48000]


def test_audio_ack_does_not_wait_for_translation(tmp_path):
    translating, release = threading.Event(), threading.Event()

    class SlowTranslation(Incremental):
        def transcribe(self, *args):
            super().transcribe(*args)
            return 'This sentence needs translation.'

        def translate(self, *args):
            translating.set()
            assert release.wait(5)
            return super().translate(*args)

    with TestClient(create_app(Settings(data_dir=tmp_path, provider='qwen'), SlowTranslation())) as client:
        mid = create(client)
        with client.websocket_connect(f'/ws/{mid}') as ws:
            ws.receive_json(); ws.send_json(packet(uuid4().hex, 1, final=True))
            try:
                until(ws, lambda e: e['type'] == 'audio_done')
                assert translating.wait(3)
                assert client.get(f'/api/meetings/{mid}').json()['segments'][0]['translation'] == ''
            finally:
                release.set()
            ws.send_json({'type': 'finish'})
            until(ws, lambda e: e['type'] == 'finished')


@pytest.mark.parametrize('finish', [False, True])
def test_disconnect_or_finish_preserves_last_accepted_preview(tmp_path, finish):
    provider = Incremental()
    with TestClient(create_app(Settings(data_dir=tmp_path, provider='qwen'), provider)) as client:
        mid, uid = create(client), uuid4().hex
        with client.websocket_connect(f'/ws/{mid}') as ws:
            ws.receive_json(); ws.send_json(packet(uid, 1))
            until(ws, lambda e: e['type'] == 'transcript_partial')
            if finish:
                ws.send_json({'type': 'finish'})
                until(ws, lambda e: e['type'] == 'finished')
        for _ in range(200):
            meeting = client.get(f'/api/meetings/{mid}').json()
            if meeting['segments']:
                break
            time.sleep(.01)
        assert len(meeting['segments']) == 1
        assert (tmp_path / meeting['segments'][0]['audio_path']).is_file()
        assert provider.calls == [16000]


def test_failed_final_does_not_save_draft_as_confirmed_text(tmp_path):
    class Failing(Incremental):
        def transcribe(self, *args):
            text = super().transcribe(*args)
            if self.calls[-1] > 16000:
                raise RuntimeError('Final ASR failed')
            return text

    with TestClient(create_app(Settings(data_dir=tmp_path, provider='qwen'), Failing())) as client:
        mid, uid = create(client), uuid4().hex
        with client.websocket_connect(f'/ws/{mid}') as ws:
            ws.receive_json(); ws.send_json(packet(uid, 1))
            until(ws, lambda e: e['type'] == 'transcript_partial')
            ws.send_json(packet(uid, 2, final=True))
            assert until(ws, lambda e: e['type'] == 'error')['message'] == 'Final ASR failed'
            until(ws, lambda e: e['type'] == 'audio_done')
        assert client.get(f'/api/meetings/{mid}').json()['segments'] == []
        assert list((tmp_path / 'audio').rglob('*.wav'))


@pytest.mark.parametrize(('text', 'expected'), [
    ('第一句。第二句！第三句？', ['第一句。', '第二句！', '第三句？']),
    ('他说：“先测试。”然后继续。', ['他说：“先测试。”', '然后继续。']),
    ('Dr. Smith uses ROCm 7.13. It works!', ['Dr. Smith uses ROCm 7.13.', 'It works!']),
    ('We use U.S. data, e.g. audio. Next?', ['We use U.S. data, e.g. audio.', 'Next?']),
    ('Qwen3.0 有三个步骤，首先识别，然后翻译。', ['Qwen3.0 有三个步骤，首先识别，然后翻译。']),
    ('没有标点的原文', ['没有标点的原文']),
])
def test_sentence_boundaries(text, expected):
    assert split_sentences(text) == expected
