import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from evaluation_metrics import audit_trial, corpus_error, describe, error_counts, units, validate_rating


def test_known_edit_counts_and_empty_hypothesis():
    row = error_counts('the cat sat on the mat', 'the cat sit on the')
    assert (row['substitutions'], row['deletions'], row['insertions']) == (1, 1, 0)
    assert row['rate'] == pytest.approx(2 / 6)
    assert error_counts('a b', '')['rate'] == 1
    assert error_counts('a', 'a b c')['rate'] == 2  # WER is not bounded by 1.
    assert error_counts('hello', 'hello 嗯')['insertions'] == 1
    assert error_counts('hello', '你好')['substitutions'] == 1


def test_corpus_uses_reference_unit_weights_and_keeps_languages_separate():
    short = error_counts('a', 'b')
    long = error_counts('a b c d e f g h i', 'a b c d e f g h i')
    assert corpus_error([short, long])['rate'] == .1
    assert error_counts('我们开会。', '我们会开', 'zh')['rate'] == .5
    assert units('IT’S ２０２６. <noise>', 'en') == ["it's", '2026']
    assert error_counts("i 'm sure they do n't know", "I'm sure they don't know")['rate'] == 0
    with pytest.raises(ValueError):
        error_counts('', '')


def test_stats_and_rating_validation():
    assert describe([None, 1, 3, 2])['median'] == 2
    assert describe([]) == {'n': 0}
    assert validate_rating(dict(faithfulness=4, terminology=3, fluency=5, evidence='Example')) == 4
    with pytest.raises(ValueError):
        validate_rating(dict(faithfulness=True, terminology=3, fluency=5, evidence='Example'))


def test_chinese_filler_is_target_output_and_real_translation_failure_is_retained():
    row = dict(language='en', reference='Hello', quality={}, transcript='嗯。 Hello.', translation='你好。',
               success=False, errors=['Missing English-to-Chinese translation'],
               segments=[dict(text='嗯。', translation='', translation_skipped=True),
                         dict(text='Hello.', translation='你好。', translation_skipped=False)])
    result = audit_trial(row)
    assert result['success'] and result['translation'] == '嗯。\n你好。'
    assert row['success'] is False  # Never rewrite raw data in place.
    row['segments'][1]['translation'] = ''
    row['errors'].append('Actual model failure')
    result = audit_trial(row)
    assert not result['success'] and 'Actual model failure' in result['errors']


def test_file_latency_tracks_confirmed_text_not_upload_ack(tmp_path):
    """A real HTTP/WS round trip; synthetic provider is only for verifying the timer."""
    import asyncio
    import hashlib
    import socket
    import threading
    import time

    import httpx
    import numpy as np
    import uvicorn
    from app.audio import write_pcm
    from app.config import Settings
    from app.main import create_app
    from evaluation_server import add_observer
    from run_evaluation import measure

    class Provider:
        name = 'qwen'

        def transcribe(self, *args):
            time.sleep(.08)
            return 'Hello, world.'

        def translate(self, *args):
            time.sleep(.12)
            return '你好，世界。'

        def summarize(self, *args):
            time.sleep(.03)
            return '• Hello, world.'

    app = create_app(Settings(data_dir=tmp_path / 'runtime'), Provider())
    add_observer(app)
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    bound = socket.socket()
    bound.bind(('127.0.0.1', 0))
    port = bound.getsockname()[1]
    thread = threading.Thread(target=server.run, kwargs={'sockets': [bound]}, daemon=True)
    thread.start()
    audio = tmp_path / 'sample.wav'
    write_pcm(audio, (np.sin(np.arange(16000) * .05) * 8000).astype('<i2').tobytes())
    sample = dict(id='timer-fixture', group='test-only', language='en', reference='Hello world',
                  wav=audio.name, duration_seconds=1, sha256=hashlib.sha256(audio.read_bytes()).hexdigest())

    async def check():
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', timeout=5, trust_env=False) as client:
            for _ in range(100):
                if server.started:
                    break
                await asyncio.sleep(.01)
            result = await measure(client, f'http://127.0.0.1:{port}', sample, tmp_path, 'warm', 1, 'pause', 5)
            assert result['success'] and result['quality']['rate'] == 0
            assert result['upload_ack_ms'] < result['first_asr_ms'] < result['first_translation_ms'] < result['complete_ms']
            assert result['summaries']

    try:
        asyncio.run(check())
    finally:
        server.should_exit = True
        thread.join(5)
        bound.close()


def test_gpu_run_rejects_cpu_configuration_before_creating_results(tmp_path, monkeypatch):
    import asyncio
    from argparse import Namespace
    from app.config import Settings
    from run_evaluation import run

    monkeypatch.setattr(Settings, 'from_env', lambda: Settings(device='cpu'))
    output = tmp_path / 'output'
    with pytest.raises(SystemExit, match='DEVICE=cuda'):
        asyncio.run(run(Namespace(cpu=False, suite=tmp_path / 'missing-suite', output=output)))
    assert not output.exists()
