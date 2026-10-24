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
