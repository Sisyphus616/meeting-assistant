"""Run real inference without the web server; emits timings for reproducible checks.

Usage: .venv/bin/python scripts/model_smoke.py [--audio /path/to/sample.wav]
"""
import argparse
import json
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
from app.config import Settings
from app.providers import QwenProvider

parser = argparse.ArgumentParser()
parser.add_argument('--audio', type=Path)
parser.add_argument('--asr-only', action='store_true')
args = parser.parse_args()
settings = Settings.from_env()
provider = QwenProvider(settings)
results = {'device': settings.device, 'dtype': settings.dtype,
           'asr_model': settings.asr_model, 'llm_model': settings.llm_model,
           'translation_model': settings.translation_model,
           'note': 'Cold-call times include model loading. These are not end-to-end subtitle latency.'}
text = '我们决定先在本地完成开发，然后在 AMD 平台上测试模型性能。'
if args.audio:
    started = time.perf_counter()
    text = provider.transcribe(args.audio.resolve(), 'auto', 'AMD, Qwen, ROCm')
    results['asr'] = {'text': text, 'cold_call_seconds': round(time.perf_counter()-started,3)}
if not args.asr_only:
    started = time.perf_counter()
    results['translation'] = {'text': provider.translate(text, 'en', '', 'AMD, Qwen, ROCm'),
                              'cold_call_seconds': round(time.perf_counter()-started,3)}
    started = time.perf_counter()
    results['summary'] = {'text': provider.summarize([{'id':1,'text':text}], 'AMD, Qwen, ROCm'),
                         'call_seconds': round(time.perf_counter()-started,3)}
provider.close()
print(json.dumps(results, ensure_ascii=False, indent=2))
