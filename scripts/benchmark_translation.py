"""Fixed sentence sanity checks; not an accuracy benchmark or a public ranking."""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from app.config import Settings
from app.translation import HyMTTranslator

SAMPLES = [
    ('甚至出现交易几乎停滞的情况。','en'),
    ('我们决定先在本地完成前后端开发，等 AMD 平台资源恢复后，再验证 GPU 上的实时性能。','en'),
    ('识别、翻译和纪要共享计算资源，因此识别优先级最高，纪要在后台排队执行。','en'),
    ('会议暂定于下周三下午两点举行，请在周二前提交实验结果。','en'),
    ('Recognition, translation, and summarization share compute resources, so recognition has the highest priority.','zh'),
    ("He didn't say the experiment had failed; he said the results were inconclusive.",'zh'),
]
parser = argparse.ArgumentParser()
parser.add_argument('--output',type=Path,default=ROOT/'artifacts/translation-hymt2.json')
args = parser.parse_args()
translator = HyMTTranslator(Settings.from_env())
results = []
try:
    for text,target in SAMPLES:
        started = time.perf_counter()
        row = {'source':text,'target':target,'translation':translator.translate(text,target,'',''),
               'seconds':round(time.perf_counter()-started,3)}
        results.append(row)
        print(json.dumps(row,ensure_ascii=False),flush=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(results,ensure_ascii=False,indent=2))
finally:
    translator.close()
