import pytest

from app.config import Settings
from app.providers import QwenProvider


def test_extractive_summary_renders_only_verified_source_text(tmp_path):
    provider = QwenProvider(Settings(data_dir=tmp_path))
    provider._chat = lambda *args: '2,1,2'
    segments = [{'id':10,'text':'第一条实际原文。'}, {'id':11,'text':'第二条实际原文。'}]
    assert provider.summarize(segments,'irrelevant') == '• 第二条实际原文。\n• 第一条实际原文。'


@pytest.mark.parametrize('response',['9','1,999','补充任务：明天完成实验','1. 建议继续研究',''])
def test_extractive_summary_does_not_render_invalid_model_prose(tmp_path,response):
    provider = QwenProvider(Settings(data_dir=tmp_path))
    provider._chat = lambda *args: response
    result = provider.summarize([{'id':10,'text':'唯一原文。'}],'')
    assert result.endswith('• 唯一原文。')
    if response not in ('1. 建议继续研究',):
        assert '引用校验未通过' in result
    assert '明天' not in result and '研究' not in result


def test_extractive_accepts_numbered_or_punctuated_selection(tmp_path):
    provider = QwenProvider(Settings(data_dir=tmp_path))
    provider._chat = lambda *args: '1。'
    assert provider.summarize([{'id':10,'text':'唯一原文。'}],'') == '• 唯一原文。'


def test_recording_preparation_warms_other_models_once_then_asr_last(tmp_path):
    provider = QwenProvider(Settings(data_dir=tmp_path))
    calls = []
    provider.translator.translate = lambda *a: calls.append('translation') or 'Hello.'
    provider._chat = lambda *a: calls.append('minutes') or '1'
    provider.prepare_asr = lambda: calls.append('asr')
    assert provider.prepare_recording() == []
    assert calls == ['translation', 'minutes', 'asr']
    assert provider.prepare_recording() == []
    assert calls == ['translation', 'minutes', 'asr', 'asr']


def test_failed_optional_warmup_does_not_disable_transcription(tmp_path):
    provider = QwenProvider(Settings(data_dir=tmp_path))
    def fail(*a):
        raise RuntimeError('Translation unavailable')
    provider.translator.translate = fail
    provider._chat = lambda *a: '1'
    prepared = []
    provider.prepare_asr = lambda: prepared.append(True)
    warnings = provider.prepare_recording()
    assert len(warnings) == 1 and 'Translation unavailable' in warnings[0]
    assert prepared and not provider.translation_ready


@pytest.mark.parametrize(('last_token', 'limit', 'truncated'), [(1, 4, True), (99, 4, False), (1, 1, False)])
def test_asr_rejects_truncated_output_without_breaking_warmup(tmp_path, last_token, limit, truncated):
    from contextlib import nullcontext
    from types import SimpleNamespace
    import numpy as np
    class Batch(dict):
        def to(self, *args):
            return self
    provider = QwenProvider(Settings(data_dir=tmp_path))
    provider._torch = lambda: SimpleNamespace(inference_mode=nullcontext)
    output = np.array([[0, 0] + [1] * (limit - 1) + [last_token]])
    provider.asr = SimpleNamespace(generation_config=SimpleNamespace(eos_token_id=99), generate=lambda **kw: output)
    provider.processor = SimpleNamespace(
        apply_transcription_request=lambda **kw: Batch(input_ids=np.array([[0, 0]])),
        decode=lambda *a, **kw: ['A complete transcript.'])
    if truncated:
        with pytest.raises(RuntimeError, match='输出超过长度上限'):
            provider._decode(tmp_path / 'test.wav', 'en', '', max_tokens=limit)
    else:
        assert provider._decode(tmp_path / 'test.wav', 'en', '', max_tokens=limit) == 'A complete transcript.'
