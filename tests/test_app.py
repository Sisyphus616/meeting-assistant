import asyncio
import base64
import io
import json
import threading
import wave

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.providers import DEMO
from app.scheduler import InferenceQueue


def settings(tmp_path, **kwargs):
    return Settings(data_dir=tmp_path, **kwargs)


def create(client, **kwargs):
    response = client.post('/api/meetings', json={'title':'测试课堂', **kwargs})
    assert response.status_code == 201
    return response.json()['id']


def until(socket, predicate, limit=100):
    for _ in range(limit):
        event = socket.receive_json()
        if predicate(event):
            return event
    raise AssertionError('Expected WebSocket event not received')


def test_demo_pipeline_exports_and_restart(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_app(config)) as client:
        assert client.get('/').status_code == 200
        assert client.get('/static/app.js').status_code == 200
        assert client.get('/api/health').json()['provider'] == 'demo'
        mid = create(client)
        with client.websocket_connect(f'/ws/{mid}') as ws:
            assert ws.receive_json()['type'] == 'snapshot'
            for _ in range(8):
                ws.send_json({'type':'demo_next'})
            ws.send_json({'type':'finish'})
            until(ws, lambda event: event['type'] == 'finished')
        meeting = client.get(f'/api/meetings/{mid}').json()
        assert meeting['status'] == 'completed'
        assert [s['text'] for s in meeting['segments']] == [row[0] for row in DEMO]
        assert [s['translation'] for s in meeting['segments']] == [row[1] for row in DEMO]
        covered = [sid for summary in meeting['summaries'] for sid in summary['segment_ids']]
        assert sorted(covered) == [s['id'] for s in meeting['segments']]
        assert len(covered) == len(set(covered))
        for kind in ['md','json','srt']:
            response = client.get(f'/api/meetings/{mid}/export?format={kind}')
            assert response.status_code == 200
            assert 'attachment' in response.headers['content-disposition']
        assert ' --> ' in client.get(f'/api/meetings/{mid}/export?format=srt').text
        assert client.get(f'/api/meetings/{mid}/export?format=json').json() == meeting
    with TestClient(create_app(config)) as client:
        assert client.get(f'/api/meetings/{mid}').json() == meeting


def test_malformed_messages_do_not_disconnect(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        mid = create(client)
        with client.websocket_connect(f'/ws/{mid}') as ws:
            ws.receive_json()
            for message in ['invalid JSON', '[]', '{"type":"unknown"}', '{"type":"audio","pcm16":"bad"}']:
                ws.send_text(message)
                event = until(ws, lambda e: e['type'] == 'error')
                assert event['message']
            ws.send_json({'type':'ping'})
            assert until(ws, lambda e: e['type']=='metrics')['metrics']['completed'] == 0


def test_active_meeting_becomes_interrupted_on_restart(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_app(config)) as client:
        mid = create(client)
    with TestClient(create_app(config)) as client:
        assert client.get(f'/api/meetings/{mid}').json()['status'] == 'interrupted'
        with client.websocket_connect(f'/ws/{mid}') as ws:
            ws.receive_json()
            ws.send_json({'type':'text','text':'不能再写入已中断会议'})
            assert until(ws,lambda e:e['type']=='error')['message']


class FakeQwen:
    name = 'qwen'

    def transcribe(self, path, source, glossary):
        with wave.open(str(path)) as audio:
            assert audio.getframerate() == 16000
            assert audio.getnchannels() == 1
        return '这是实际音频路径的接口测试。'

    def translate(self, text, target, context, glossary):
        return 'This is an audio pipeline integration test.'

    def summarize(self, segments, glossary):
        return '• 完成音频管线测试。'


def wav_blob(rate=16000, channels=1, seconds=1):
    output = io.BytesIO()
    with wave.open(output,'wb') as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b'\x00\x08' * rate * channels * seconds)
    return output.getvalue()


def test_audio_ws_persistence_playback_and_invalid_audio(tmp_path):
    with TestClient(create_app(settings(tmp_path, provider='qwen'), FakeQwen())) as client:
        mid = create(client)
        with client.websocket_connect(f'/ws/{mid}') as ws:
            ws.receive_json()
            ws.send_json({'type':'audio','sample_rate':16000,'pcm16':'not-base64','start_ms':0})
            until(ws,lambda e:e['type']=='error')
            pcm = base64.b64encode(b'\x00\x08' * 16000).decode()
            ws.send_json({'type':'audio','sample_rate':16000,'pcm16':pcm,'start_ms':1200})
            until(ws,lambda e:e['type']=='audio_done')
            ws.send_json({'type':'finish'})
            until(ws,lambda e:e['type']=='finished')
        meeting = client.get(f'/api/meetings/{mid}').json()
        segment = meeting['segments'][0]
        assert segment['start_ms'] == 1200 and segment['end_ms'] == 2200
        assert segment['translation'] and segment['asr_ms'] is not None
        response = client.get(f'/api/meetings/{mid}/audio/{segment["id"]}')
        assert response.status_code == 200 and response.content[:4] == b'RIFF'
        assert client.get(f'/api/meetings/{mid}/audio/9999').status_code == 404


def test_wav_upload_resample_and_final_drain(tmp_path):
    with TestClient(create_app(settings(tmp_path, provider='qwen'), FakeQwen())) as client:
        mid = create(client)
        response = client.post(f'/api/meetings/{mid}/upload',files={'file':('bad.wav',b'invalid')})
        assert response.status_code == 400
        with client.websocket_connect(f'/ws/{mid}') as ws:
            ws.receive_json()
            response = client.post(f'/api/meetings/{mid}/upload',files={'file':('test.wav',wav_blob(44100,2,11))})
            assert response.status_code == 200
            assert response.json()['duration_ms'] == 11000
            ws.send_json({'type':'finish'})
            until(ws,lambda e:e['type']=='finished')
        meeting = client.get(f'/api/meetings/{mid}').json()
        assert len(meeting['segments']) == 2
        assert meeting['segments'][-1]['end_ms'] == 11000
        assert len(meeting['summaries']) >= 1


def test_translation_errors_are_persisted_not_faked(tmp_path):
    class Failing(FakeQwen):
        def translate(self, *args):
            raise RuntimeError('model unavailable')
    with TestClient(create_app(settings(tmp_path, provider='qwen'), Failing())) as client:
        mid = create(client)
        with client.websocket_connect(f'/ws/{mid}') as ws:
            ws.receive_json()
            ws.send_json({'type':'text','text':'测试错误'})
            until(ws,lambda e:e['type']=='error')
            ws.send_json({'type':'finish'})
            until(ws,lambda e:e['type']=='finished')
        segment = client.get(f'/api/meetings/{mid}').json()['segments'][0]
        assert segment['translation'] == ''
        assert segment['error'] == 'model unavailable'


def test_input_validation_and_missing_meeting(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.get('/api/meetings/missing').status_code == 404
        assert client.post('/api/meetings',json={'source':'invalid'}).status_code == 422
        mid = create(client)
        assert client.get(f'/api/meetings/{mid}/export?format=html').status_code == 422
        assert client.post(f'/api/meetings/{mid}/upload',files={'file':('test.wav',wav_blob())}).status_code == 400


def test_priority_queue_waits_for_active_then_runs_asr_first():
    async def scenario():
        queue = InferenceQueue(8)
        queue.start()
        started, release = threading.Event(), threading.Event()
        order = []
        def active():
            started.set()
            release.wait(3)
            order.append('active summary')
        def work(name):
            order.append(name)
            return name
        first = asyncio.create_task(queue.submit(2,'纪要',active))
        while not started.is_set():
            await asyncio.sleep(.005)
        tasks = [asyncio.create_task(queue.submit(priority, name, work, name)) for priority,name in [
            (2,'queued summary'),(1,'translation'),(0,'ASR')]]
        await asyncio.sleep(.02)
        release.set()
        await asyncio.gather(first,*tasks)
        assert order == ['active summary','ASR','translation','queued summary']
        assert queue.metrics()['completed'] == 4
        await queue.close()
    asyncio.run(scenario())
