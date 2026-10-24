import httpx
import pytest

from app.config import Settings
from app.providers import QwenProvider
from app.translation import HyMTTranslator, translation_prompt


def test_translation_is_independent_of_summary_llm(tmp_path):
    provider = QwenProvider(Settings(data_dir=tmp_path))
    provider._chat = lambda *args: pytest.fail('Translation must not call the generic summary LLM')
    provider.translator.translate = lambda text, target, context, glossary: 'Specialized translation'
    assert provider.translate('原文','en','','') == 'Specialized translation'


def test_hymt_prompt_uses_language_names_and_isolates_context():
    prompt = translation_prompt('待译原文','en','之前的话','ROCm')
    assert '英语' in prompt and '背景信息' in prompt and 'ROCm' in prompt
    assert prompt.endswith('\n\n待译原文')
    assert '中文' in translation_prompt('Source','zh')


def test_http_adapter_uses_only_user_message_and_disables_proxy(monkeypatch,tmp_path):
    seen = {}
    class Client:
        def __init__(self,**kwargs): seen['client']=kwargs
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def post(self,url,**kwargs):
            seen['url'],seen['payload']=url,kwargs['json']
            return httpx.Response(200,request=httpx.Request('POST',url),json={'choices':[
                {'finish_reason':'stop','message':{'content':'A faithful translation.'}}]})
    monkeypatch.setattr('app.translation.httpx.Client',Client)
    settings = Settings(data_dir=tmp_path,translation_backend='compatible')
    result = HyMTTranslator(settings).translate('原文','en','','')
    assert result == 'A faithful translation.'
    assert seen['client']['trust_env'] is False
    assert [m['role'] for m in seen['payload']['messages']] == ['user']
    assert seen['payload']['model'] == 'tencent/Hy-MT2-1.8B'
    assert seen['url'].endswith('/v1/chat/completions')


def test_missing_model_is_an_error_not_a_qwen_fallback(tmp_path):
    translator = HyMTTranslator(Settings(data_dir=tmp_path,translation_binary=tmp_path/'missing',translation_gguf=tmp_path/'missing.gguf'))
    with pytest.raises(RuntimeError,match='缺少翻译模型'):
        translator.translate('原文','en','','')


@pytest.mark.parametrize('content',['','<think>unfinished'])
def test_empty_or_incomplete_translation_is_rejected(tmp_path,content):
    translator = HyMTTranslator(Settings(data_dir=tmp_path,translation_backend='compatible'))
    translator._http = lambda prompt: content
    with pytest.raises(RuntimeError,match='完整译文'):
        translator.translate('原文','en','','')
