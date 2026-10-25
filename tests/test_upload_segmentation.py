import io
import json
import wave

import numpy as np
from fastapi.testclient import TestClient

from app.audio import SAMPLE_RATE, split_upload
from app.main import create_app
from tests.test_app import FakeQwen, create, settings, until


def tone(seconds):
    t = np.arange(round(seconds * SAMPLE_RATE))
    return (np.sin(t * .1) * 3000).astype('<i2')


def silence(seconds):
    return np.zeros(round(seconds * SAMPLE_RATE), dtype='<i2')


def blob(samples):
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(SAMPLE_RATE)
        wav.writeframes(samples.astype('<i2').tobytes())
    return output.getvalue()


def assert_voiced_samples_preserved(samples, spans):
    covered = np.zeros(len(samples), dtype=bool)
    previous_end = 0
    for span in spans:
        assert previous_end <= span.start < span.end <= len(samples)
        covered[span.start:span.end] = True
        previous_end = span.end
    assert covered[np.abs(samples) > 300].all()


def test_natural_pauses_choose_boundaries_instead_of_ten_second_grid():
    samples = np.concatenate([tone(3.7), silence(.7), tone(5.1), silence(.6), tone(3.3)])
    spans = split_upload(samples.tobytes())
    assert len(spans) == 3
    assert 3.7 <= spans[0].end / SAMPLE_RATE <= 4.1
    assert 9.5 <= spans[1].end / SAMPLE_RATE <= 9.8
    assert_voiced_samples_preserved(samples, spans)


def test_eleven_second_sentence_and_short_hesitation_are_not_cut():
    continuous = tone(11)
    assert len(split_upload(continuous.tobytes())) == 1
    samples = np.concatenate([tone(6), silence(.1), tone(6)])
    spans = split_upload(samples.tobytes())
    assert len(spans) == 1 and spans[0].start == 0 and spans[0].end == len(samples)


def test_brief_tail_sound_is_kept_with_context_not_a_separate_sentence():
    samples = np.concatenate([tone(3), silence(.5), tone(.02), silence(.5)])
    spans = split_upload(samples.tobytes())
    assert len(spans) == 1
    assert_voiced_samples_preserved(samples, spans)


def test_long_speech_uses_a_short_pause_before_the_ceiling():
    samples = np.concatenate([tone(27), silence(.16), tone(9)])
    spans = split_upload(samples.tobytes())
    assert len(spans) == 2
    assert spans[0].boundary == 'short_pause'
    assert 27 < spans[0].end / SAMPLE_RATE < 27.16
    assert all(s.end - s.start <= 30 * SAMPLE_RATE for s in spans)
    assert_voiced_samples_preserved(samples, spans)


def test_no_pause_fallback_is_bounded_without_losing_samples():
    samples = tone(65)
    spans = split_upload(samples.tobytes())
    assert len(spans) == 3
    assert all(s.end - s.start <= 30 * SAMPLE_RATE for s in spans)
    assert spans[0].boundary == 'length'
    assert b''.join(samples[s.start:s.end].tobytes() for s in spans) == samples.tobytes()


def test_offsets_and_quiet_speech_are_retained_and_silence_has_no_asr():
    samples = np.concatenate([silence(2), (tone(2) * .02).astype('<i2'), silence(3)])
    spans = split_upload(samples.tobytes())
    assert len(spans) == 1 and 1.7 < spans[0].start / SAMPLE_RATE < 2
    assert 4 <= spans[0].end / SAMPLE_RATE < 4.3
    assert split_upload(silence(5).tobytes()) == []


def test_upload_persists_complete_source_and_records_actual_clip_times(tmp_path):
    samples = np.concatenate([silence(1), tone(3.7), silence(.7), tone(5.1), silence(2)])
    with TestClient(create_app(settings(tmp_path, provider='qwen'), FakeQwen())) as client:
        mid = create(client)
        with client.websocket_connect(f'/ws/{mid}') as ws:
            ws.receive_json()
            result = client.post(f'/api/meetings/{mid}/upload', files={'file': ('speech.wav', blob(samples))})
            assert result.status_code == 200
            assert result.json()['segmentation'] == 'pause-first'
            assert result.json()['chunk_count'] == 2 and result.json()['forced_chunks'] == 0
            ws.send_json({'type': 'finish'})
            until(ws, lambda e: e['type'] == 'finished')
        segments = client.get(f'/api/meetings/{mid}').json()['segments']
        assert len(segments) == 2
        assert 700 < segments[0]['start_ms'] < 1000
        assert 10500 <= segments[1]['end_ms'] < 10800
        directory = next((tmp_path / 'audio' / mid / 'imports').iterdir())
        with wave.open(str(directory / 'source.wav')) as original:
            assert original.readframes(original.getnframes()) == samples.tobytes()
        plan = json.loads((directory / 'manifest.json').read_text())
        assert plan['duration_ms'] == round(len(samples) / 16)
        assert plan['clips'][0]['boundary'] == 'pause'


def test_silent_upload_is_rejected_before_scheduling_a_model(tmp_path):
    with TestClient(create_app(settings(tmp_path, provider='qwen'), FakeQwen())) as client:
        mid = create(client)
        r = client.post(f'/api/meetings/{mid}/upload', files={'file': ('silence.wav', blob(silence(2)))})
        assert r.status_code == 400
        assert '未检测到' in r.json()['detail']
        assert client.get('/api/health').json()['metrics']['completed'] == 0
