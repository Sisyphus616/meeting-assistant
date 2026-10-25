"""Download a fixed, small public speech evaluation suite; never runs inference."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import random
import re
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import httpx
import numpy as np
import soundfile as sf
from app.audio import normalize_wav, write_pcm

SEED = 20261026
SOURCES = {
    'ami': dict(dataset='edinburghcstr/ami', config='ihm', split='test',
                homepage='https://groups.inf.ed.ac.uk/ami/corpus/', license='CC-BY-4.0'),
    'ted': dict(dataset='distil-whisper/tedlium-dev-test', config='default', split='test',
                homepage='https://www.openslr.org/19/', license='CC-BY-NC-ND-3.0'),
    'zh': dict(dataset='google/fleurs', config='cmn_hans_cn', split='validation',
               homepage='https://huggingface.co/datasets/google/fleurs', license='CC-BY-4.0'),
}


def request(url, **kwargs):
    for attempt in range(4):
        try:
            response = httpx.get(url, follow_redirects=True, timeout=90, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            if attempt == 3:
                raise
            limited = isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429
            time.sleep(20 * (attempt + 1) if limited else 2 ** attempt)


def fetch_rows(source, offset, count, cache):
    if cache.exists():
        return json.loads(cache.read_text())
    response = request('https://datasets-server.huggingface.co/rows', params={
        **{key: source[key] for key in ('dataset', 'config', 'split')},
        'offset': offset, 'length': count})
    data = response.json()
    cache.write_text(json.dumps(data, ensure_ascii=False))
    return data


def source_duration(row, name):
    if name == 'ami':
        return row['end_time'] - row['begin_time']
    if name == 'ted':
        match = re.search(r'-(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)-', row['id'])
        return float(match[2]) - float(match[1]) if match else 0
    return row.get('num_samples', 0) / 16000


def prepare(directory, plan_path=None):
    plan = json.loads(plan_path.read_text()) if plan_path else None
    if (directory / 'manifest.json').exists():
        manifest = json.loads((directory / 'manifest.json').read_text())
        if plan and {r['id']: r['sha256'] for r in manifest['samples']} != {r['id']: r['sha256'] for r in plan['samples']}:
            raise RuntimeError('Existing suite differs from pinned plan')
        for row in manifest['samples']:
            if hashlib.sha256((directory / row['wav']).read_bytes()).hexdigest() != row['sha256']:
                raise RuntimeError(f'Existing audio changed: {row["id"]}')
        print(f'Existing verified suite: {len(manifest["samples"])} samples; {directory / "manifest.json"}')
        return
    directory.mkdir(parents=True, exist_ok=True)
    cache = directory / 'cache'
    cache.mkdir(exist_ok=True)
    selected = []
    provenance = {}
    for name, source in SOURCES.items():
        metadata = request(f'https://huggingface.co/api/datasets/{source["dataset"]}').json()
        if plan and metadata['sha'] != plan['sources'][name]['revision']:
            raise RuntimeError(f'{name}: upstream revision changed; transfer the existing verified suite '
                               'instead of silently resampling a different dataset snapshot')
        source = dict(source, revision=metadata['sha'])
        provenance[name] = source
        if plan:
            wanted = [row for row in plan['samples'] if row['source'] == name and not row.get('derived_from')]
            cached = {}
            for path in cache.glob(f'{name}-*.json'):
                cached.update({item['row_idx']: item for item in json.loads(path.read_text()).get('rows', [])})

            def pinned(row):
                index = row['source_row']
                if index in cached:
                    return cached[index]
                return fetch_rows(source, index, 1, cache / f'{name}-{index}.json')['rows'][0]

            with ThreadPoolExecutor(max_workers=3) as pool:
                candidates = list(pool.map(pinned, wanted))
        else:
            first = fetch_rows(source, 0, 100, cache / f'{name}-0.json')
            total = first['num_rows_total']
            candidates = list(first['rows'])
            if name == 'ted':
                offsets = list(range(100, total, 100))
            elif name == 'ami':
                offsets = [int(n) for n in np.linspace(300, total - 100, 15)]
            else:
                offsets = []

            def fetch(offset):
                return fetch_rows(source, offset, min(100, total - offset), cache / f'{name}-{offset}.json')['rows']

            with ThreadPoolExecutor(max_workers=4) as pool:
                for rows in pool.map(fetch, offsets):
                    candidates.extend(rows)
            random.Random(SEED + {'ami': 1, 'ted': 2, 'zh': 3}[name]).shuffle(candidates)
        counts = {}
        picked = 0
        desired = 5 if name == 'zh' else 10
        for item in candidates:
            row = item['row']
            text = row.get('text') or row.get('raw_transcription') or row.get('transcription', '')
            duration = source_duration(row, name)
            lower = {'ami': 5, 'ted': 15, 'zh': 5}[name]
            if not lower <= duration <= 40 or not text.strip() or 'ignore_time_segment' in text:
                continue
            session = str(row.get('meeting_id') or row.get('speaker_id') or row.get('id'))
            if counts.get(session, 0) >= (2 if name == 'ami' else 1):
                continue
            counts[session] = counts.get(session, 0) + 1
            selected.append(dict(id=f'{name}-{picked + 1:02}', language='zh' if name == 'zh' else 'en',
                group={'ami': 'meeting', 'ted': 'talk', 'zh': 'chinese_auxiliary'}[name],
                reference=text.strip(), source=name, source_revision=source['revision'],
                source_row=item['row_idx'], session=session, speaker=row.get('speaker_id'),
                source_begin_seconds=row.get('begin_time'), source_end_seconds=row.get('end_time'),
                dataset_duration_seconds=duration, audio_url=row['audio'][0]['src']))
            picked += 1
            if picked == desired:
                break
        if picked != desired:
            raise RuntimeError(f'{name}: only {picked}/{desired} eligible samples; do not silently change suite')
        print(f'{name}: selected {picked} reference-labelled recordings', flush=True)

    def download(row):
        url = row.pop('audio_url')
        if row['source_revision'] not in url:
            raise RuntimeError('Dataset viewer audio revision differs from dataset metadata; retry with a consistent snapshot')
        raw = request(url).content
        audio, rate = sf.read(io.BytesIO(raw), dtype='float32')
        buffer = io.BytesIO()
        sf.write(buffer, audio, rate, format='WAV', subtype='PCM_16')
        pcm = normalize_wav(buffer.getvalue())
        path = directory / 'audio' / f'{row["id"]}.wav'
        write_pcm(path, pcm)
        row.update(wav=str(path.relative_to(directory)), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                   duration_seconds=len(pcm) / 32000, source_audio_url=url.split('?')[0])
        if plan and row['sha256'] != next(item['sha256'] for item in plan['samples'] if item['id'] == row['id']):
            raise RuntimeError(f'Pinned audio hash differs: {row["id"]}')
        print(f'{row["id"]}: {row["duration_seconds"]:.2f}s', flush=True)
        return row

    with ThreadPoolExecutor(max_workers=4) as pool:
        samples = list(pool.map(download, selected))
    # Five paired stress cases, not five new independent speakers or recordings.
    for i, original in enumerate([row for row in samples if row['source'] == 'ami'][:5]):
        values, rate = sf.read(directory / original['wav'], dtype='float32')
        rng = np.random.default_rng(SEED + i)
        noise = rng.normal(size=values.shape)
        rms = np.sqrt(np.mean(values.astype('float64') ** 2))
        noise *= rms / (10 ** (10 / 20)) / np.sqrt(np.mean(noise ** 2))
        mixed = values + noise
        scale = min(1., .95 / max(float(np.max(np.abs(mixed))), 1e-8))
        path = directory / 'audio' / f'noise-{i + 1:02}.wav'
        sf.write(path, mixed * scale, rate, subtype='PCM_16')
        row = dict(original, id=f'noise-{i + 1:02}', group='meeting_noise',
                   derived_from=original['id'], noise_snr_db=10, noise_seed=SEED + i,
                   noise_note='Gaussian noise; SNR uses whole-file RMS, including pauses.',
                   wav=str(path.relative_to(directory)), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        samples.append(row)
    if plan and {r['id']: r['sha256'] for r in samples} != {r['id']: r['sha256'] for r in plan['samples']}:
        raise RuntimeError('Prepared suite does not match pinned audio hashes')
    manifest = dict(version=1, seed=SEED, sources=provenance, samples=samples,
        selection='Pre-inference fixed selection: 10 AMI headset meeting clips (5–40s), '
                  '10 TED-LIUM held-out talk clips (15–40s), 5 auxiliary Mandarin FLEURS validation clips (5–40s), '
                  'and 5 paired AMI Gaussian-noise stress cases (10dB). No selection by model output.',
        limitations='25 original clips plus 5 derived pairs, not 30 independent recordings. '
                    'AMI IHM is close-talk per-speaker audio, not a mixed far-field meeting. '
                    'TED talks are presentations, not all are academic lectures. '
                    'Chinese FLEURS is read speech, not a meeting. Public reference transcripts have not been manually re-audited. '
                    'Dataset overlap with model pretraining is unknown. No human translations are supplied.')
    (directory / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f'Suite ready: {directory / "manifest.json"}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT / 'artifacts/evaluation/suite')
    parser.add_argument('--plan', type=Path, default=ROOT / 'configs/evaluation-suite.json')
    parser.add_argument('--resample', action='store_true', help='Intentionally create a NEW suite; not for CPU/GPU comparison')
    args = parser.parse_args()
    prepare(args.directory.resolve(), None if args.resample else args.plan.resolve())
