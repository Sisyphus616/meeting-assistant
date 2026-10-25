from fastapi.testclient import TestClient
import threading

from app.main import create_app
from tests.test_app import FakeQwen, create, settings, until


def test_retranslate_completed_meeting_preserves_source_and_notes(tmp_path):
    release = threading.Event()
    class Translator(FakeQwen):
        generation = 1
        def translate(self,*args):
            if self.generation == 2: release.wait(5)
            return f'Translation {self.generation}'
    provider = Translator()
    with TestClient(create_app(settings(tmp_path,provider='qwen'),provider)) as client:
        mid = create(client)
        with client.websocket_connect(f'/ws/{mid}') as ws:
            ws.receive_json()
            ws.send_json({'type':'text','text':'Source text requiring retranslation.'})
            ws.send_json({'type':'finish'})
            until(ws,lambda e:e['type']=='finished')
            before = client.get(f'/api/meetings/{mid}').json()
            provider.generation = 2
            ws.send_json({'type':'retranslate'})
            until(ws,lambda e:e['type']=='retranslation_started')
            with client.websocket_connect(f'/ws/{mid}') as reconnect:
                assert reconnect.receive_json()['retranslating'] is True
            release.set()
            until(ws,lambda e:e['type']=='retranslation_done')
        after = client.get(f'/api/meetings/{mid}').json()
        assert before['segments'][0]['translation'] == 'Translation 1'
        assert after['segments'][0]['translation'] == 'Translation 2'
        assert after['segments'][0]['text'] == before['segments'][0]['text']
        assert after['summaries'] == before['summaries']
        assert after['status'] == 'completed'
