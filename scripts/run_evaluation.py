"""Measure actual HTTP/WebSocket file inference on an isolated localhost server."""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from evaluation_metrics import audit_trial, error_counts

import httpx
import websockets


def elapsed(start):
    return round((time.perf_counter() - start) * 1000, 3)


async def measure(client, origin, sample, suite, phase, repeat, splitter, timeout):
    response = await client.post('/api/meetings', json={
        'title': f'实验 {splitter} {sample["id"]} {phase}-{repeat}',
        'source': 'en', 'target': 'zh', 'glossary': ''})
    response.raise_for_status()
    meeting_id = response.json()['id']
    before = (await client.get('/api/health')).json()['metrics']['completed']
    result = dict(sample_id=sample['id'], group=sample['group'], language=sample['language'],
                  splitter=splitter, phase=phase, repeat=repeat, meeting_id=meeting_id,
                  reference=sample['reference'], duration_seconds=sample['duration_seconds'],
                  audio_sha256=sample['sha256'], first_asr_ms=None, first_translation_ms=None,
                  errors=[], events=[])
    audio = (suite / sample['wav']).read_bytes()
    start = None
    finished = asyncio.Event()
    latest = None

    async with websockets.connect(origin.replace('http://', 'ws://') + f'/ws/{meeting_id}',
                                  max_size=4 * 1024 * 1024) as ws:
        # Subscribe before uploading; the initial empty snapshot is not an output.
        initial = json.loads(await ws.recv())
        assert initial['type'] == 'snapshot' and not initial['meeting']['segments']

        async def listen():
            nonlocal latest
            async for raw in ws:
                event = json.loads(raw)
                at = elapsed(start)
                if event['type'] == 'snapshot':
                    latest = event['meeting']
                    segments = latest['segments']
                    if result['first_asr_ms'] is None and any(s['text'].strip() for s in segments):
                        result['first_asr_ms'] = at
                    if result['first_translation_ms'] is None and any(s['translation'].strip() for s in segments):
                        result['first_translation_ms'] = at
                    result['events'].append(dict(at_ms=at, type='snapshot', segments=len(segments),
                        translated=sum(bool(s['translation'].strip()) for s in segments),
                        summaries=len(latest['summaries']), status=latest['status']))
                elif event['type'] == 'error':
                    result['errors'].append(event['message'])
                elif event['type'] == 'finished':
                    result['complete_ms'] = at
                    finished.set()

        start = time.perf_counter()
        listener = asyncio.create_task(listen())
        try:
            response = await client.post(f'/api/meetings/{meeting_id}/upload',
                                         files={'file': (f'{sample["id"]}.wav', audio, 'audio/wav')})
            result['upload_ack_ms'] = elapsed(start)
            response.raise_for_status()
            result['upload'] = response.json()
            while True:
                status = (await client.get(f'/__evaluation/status/{meeting_id}')).json()
                if not status['pending'] and not status['summary_running']:
                    break
                if elapsed(start) > timeout * 1000:
                    raise TimeoutError('File processing timeout; suite stopped to avoid overlapping trials')
                if listener.done():
                    await listener
                    raise RuntimeError('WebSocket closed before completion')
                await asyncio.sleep(.1)
            result['processing_observed_done_ms'] = elapsed(start)
            # Let normal live-file automatic summaries finish before requesting final notes.
            await ws.send(json.dumps({'type': 'finish'}))
            await asyncio.wait_for(finished.wait(), timeout)
        finally:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
    meeting = (await client.get(f'/api/meetings/{meeting_id}')).json()
    segments = meeting['segments']
    result.update(segments=segments, summaries=meeting['summaries'],
                  transcript=' '.join(s['text'] for s in segments),
                  translation='\n'.join(s['translation'] for s in segments if s['translation']),
                  translation_skipped=bool(segments) and all(s['translation_skipped'] for s in segments))
    result['errors'].extend(s['error'] for s in segments if s['error'] and s['error'] not in result['errors'])
    result['quality'] = error_counts(sample['reference'], result['transcript'], sample['language'])
    if not result['transcript'].strip():
        result['errors'].append('No confirmed transcript')
    if sample['language'] == 'en' and (not result['translation'].strip() or
            any(not s['translation'].strip() for s in segments)):
        result['errors'].append('Missing English-to-Chinese translation')
    if sample['language'] == 'zh' and not result['translation_skipped']:
        result['errors'].append('Chinese translation-skip policy failed')
    health = (await client.get('/api/health')).json()
    count = health['metrics']['completed'] - before
    result['inference_jobs'] = health['metrics']['samples'][-count:] if count else []
    result['inference_jobs_truncated'] = count > len(health['metrics']['samples'])
    result['pipeline_rtf'] = result['complete_ms'] / 1000 / sample['duration_seconds']
    result['success'] = not result['errors']
    return audit_trial(result)


async def wait_server(client, child):
    for _ in range(240):
        if child.poll() is not None:
            raise RuntimeError('Evaluation server exited; inspect server log')
        try:
            response = await client.get('/api/health', timeout=1)
            if response.status_code == 200:
                health = response.json()
                if health['provider'] != 'qwen' or any(health['models_loaded'].values()):
                    raise RuntimeError('Expected a fresh real-model server with unloaded models')
                return health
        except httpx.HTTPError:
            pass
        await asyncio.sleep(.25)
    raise TimeoutError('Evaluation server startup timeout')


async def run(args):
    from app.config import Settings
    settings = Settings.from_env()
    if not args.cpu and settings.device != 'auto' and not settings.device.startswith('cuda'):
        raise SystemExit('GPU 实验需要 .env 中 DEVICE=cuda（或 auto）。本地 CPU 实验请显式使用 --cpu。')
    suite = args.suite.resolve()
    manifest = json.loads((suite / 'manifest.json').read_text())
    for row in manifest['samples']:
        if hashlib.sha256((suite / row['wav']).read_bytes()).hexdigest() != row['sha256']:
            raise RuntimeError(f'Audio hash changed: {row["id"]}')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'trials.jsonl').exists():
        raise SystemExit('Output already contains trials. Use a new --output to preserve previous results.')
    metadata = dict(started_at=datetime.now(timezone.utc).isoformat(), host=platform.node(),
                    platform=platform.platform(), python=sys.version, cpu=platform.processor(),
                    repeats=args.repeats, cpu_requested=args.cpu,
                    suite_sha256=hashlib.sha256((suite / 'manifest.json').read_bytes()).hexdigest(),
                    packages={key: importlib.metadata.version(key) for key in
                              ('torch', 'transformers', 'fastapi', 'httpx', 'websockets')},
                    timings='Client HTTP/WebSocket monotonic clock; localhost, no AUP proxy/network. '
                            'Start immediately before upload; first ASR is confirmed nonempty text, not a preview or upload ACK. '
                            'Complete includes final notes and client observation. File job completion is polled every 100ms. '
                            'Fresh-process model-cold is separate from explicit model-prepared warm trials. OS file caches are uncontrolled.',
                    summary_policy='Normal application SUMMARY_EVERY; meeting stays live until file and automatic notes finish, '
                                   'then finish is sent to generate remaining notes.',
                    git_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                    git_dirty=bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip()),
                    servers={})
    metadata['runtime_configuration'] = dict(
        MODEL_DTYPE='bfloat16' if args.cpu else settings.dtype,
        TORCH_THREADS=settings.threads, SUMMARY_EVERY=settings.summary_every,
        SUMMARY_STYLE=settings.summary_style, TRANSLATION_BACKEND=settings.translation_backend,
        TRANSLATION_GGUF=str(settings.translation_gguf),
        TRANSLATION_BINARY=str(settings.translation_binary), LLM_BACKEND=settings.llm_backend)
    snapshot = output / 'code-snapshot'
    for relative in [*ROOT.glob('app/*.py'), *ROOT.glob('scripts/*evaluation*.py')]:
        target = snapshot / relative.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(relative.read_bytes())
    metadata['code_hashes'] = {str(path.relative_to(snapshot)): hashlib.sha256(path.read_bytes()).hexdigest()
                               for path in snapshot.rglob('*.py')}
    try:
        metadata['cpu'] = next(line.split(':', 1)[1].strip() for line in
                               Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name'))
    except (OSError, StopIteration):
        pass
    if not args.cpu:
        print('正在检查 GPU 实际运算（45秒超时）...', flush=True)
        probe = subprocess.run([sys.executable, '-u', '-c',
            'import torch,json; assert torch.version.hip and torch.cuda.is_available(); '
            'x=torch.ones(2,device="cuda")*3; torch.cuda.synchronize(); '
            'assert x.cpu().tolist()==[3.,3.]; '
            'print(json.dumps(dict(gpu=torch.cuda.get_device_name(0),torch=torch.__version__,hip=torch.version.hip)))'],
            capture_output=True, text=True, timeout=45, check=True)
        metadata['gpu_probe'] = json.loads(probe.stdout)
        print(f'GPU 计算通过：{metadata["gpu_probe"]["gpu"]}；'
              f'PyTorch {metadata["gpu_probe"]["torch"]}；ROCm {metadata["gpu_probe"]["hip"]}', flush=True)
    (output / 'environment.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    for splitter in (['pause', 'fixed10'] if args.compare else ['pause']):
        # A unique free local port; the evaluation server never exposes a public listener.
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        command = [sys.executable, str(ROOT / 'scripts/evaluation_server.py'), '--port', str(port),
                   '--data-dir', str(output / f'runtime-{splitter}'), '--splitter', splitter]
        if args.cpu:
            command.append('--cpu')
        log = (output / f'server-{splitter}.log').open('w')
        child = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            async with httpx.AsyncClient(base_url=origin, timeout=args.timeout, trust_env=False) as client:
                health = await wait_server(client, child)
                metadata['servers'][splitter] = health
                samples = manifest['samples']
                if args.limit:
                    samples = samples[:args.limit]
                if splitter == 'fixed10':
                    # Predefined before inference, independent of quality or latency results.
                    samples = [row for row in manifest['samples'] if row['group'] == 'talk'][:5]
                print(f'{splitter}: 正在执行首条冷请求，包含模型加载...', flush=True)
                cold = await measure(client, origin, samples[0], suite, 'model_cold', 0, splitter, args.timeout)
                with (output / 'trials.jsonl').open('a') as stream:
                    stream.write(json.dumps(cold, ensure_ascii=False) + '\n')
                print(f'{splitter} COLD {cold["sample_id"]}: first={cold["first_asr_ms"]}ms '
                      f'complete={cold["complete_ms"]}ms success={cold["success"]}', flush=True)
                started = time.perf_counter()
                response = await client.post('/api/asr/prepare')
                response.raise_for_status()
                prepared = response.json()
                if prepared.get('warnings'):
                    raise RuntimeError(f'Model preparation warnings: {prepared["warnings"]}')
                metadata['servers'][splitter]['prepare_ms'] = elapsed(started)
                for repeat in range(1, args.repeats + 1):
                    # Fixed order, rotated between repeats to expose order/cache sensitivity.
                    shift = (repeat - 1) * max(1, len(samples) // 3) % len(samples)
                    for sample in samples[shift:] + samples[:shift]:
                        result = await measure(client, origin, sample, suite, 'warm', repeat, splitter, args.timeout)
                        with (output / 'trials.jsonl').open('a') as stream:
                            stream.write(json.dumps(result, ensure_ascii=False) + '\n')
                        print(f'{splitter} r{repeat} {sample["id"]}: '
                              f'{result["quality"]["metric"]}={result["quality"]["rate"]:.3f} '
                              f'first={result["first_asr_ms"]}ms complete={result["complete_ms"]}ms '
                              f'success={result["success"]}', flush=True)
        finally:
            child.terminate()
            try:
                await asyncio.to_thread(child.wait, 30)
            except subprocess.TimeoutExpired:
                child.kill()
                await asyncio.to_thread(child.wait)
            log.close()
            (output / 'environment.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    metadata['finished_at'] = datetime.now(timezone.utc).isoformat()
    (output / 'environment.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    print(f'Finished: {output / "trials.jsonl"}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, default=ROOT / 'artifacts/evaluation/suite')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--cpu', action='store_true', help='Use repository CPU models; omit on AUP')
    parser.add_argument('--compare', action='store_true', help='Also test an isolated fixed-10-second baseline')
    parser.add_argument('--limit', type=int, help='Development check only; not the full experimental suite')
    args = parser.parse_args()
    if args.repeats < 1 or args.repeats > 10:
        parser.error('--repeats must be 1–10')
    asyncio.run(run(args))
