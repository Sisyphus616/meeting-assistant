import base64
import binascii
import io
import wave
from pathlib import Path
from dataclasses import dataclass
import math

import numpy as np

SAMPLE_RATE = 16000
MAX_CHUNK_SECONDS = 15


@dataclass(frozen=True)
class AudioSpan:
    start: int  # Sample indices in the original normalized upload.
    end: int
    boundary: str


def split_upload(pcm: bytes, max_seconds=30, pause_ms=350):
    """Find pause-separated speech, preserving original sample offsets.

    This is energy-based acoustic segmentation, not semantic sentence inference.
    Very long speech searches a short quiet gap before the length ceiling, then
    a low-energy region as a bounded fallback. No fixed ten-second cuts.
    """
    if not pcm or len(pcm) % 2:
        raise ValueError('音频 PCM 数据不完整。')
    if max_seconds < 5 or pause_ms < 100:
        raise ValueError('音频切分参数无效。')
    values = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768
    frame = SAMPLE_RATE // 50  # 20ms; look ahead over the whole file.
    count = math.ceil(len(values) / frame)
    padded = np.pad(values, (0, count * frame - len(values)))
    rms = np.sqrt(np.mean(padded.reshape(-1, frame) ** 2, axis=1))
    positive = rms[rms > 1.5 / 32768]
    if not positive.size:
        return []
    low, high = float(np.percentile(rms, 10)), float(np.percentile(positive, 90))
    threshold = max(1.5 / 32768, low + (high - low) * .12 if high > low * 1.5 else high * .2)
    voiced = np.flatnonzero(rms > threshold)
    if not voiced.size:
        return []
    pause_frames = math.ceil(pause_ms / 20)
    groups = np.split(voiced, np.flatnonzero(np.diff(voiced) > pause_frames) + 1)
    groups = [(int(g[0]) * frame, min(len(values), (int(g[-1]) + 1) * frame), len(g)) for g in groups]
    # Keep brief tail sounds / interjections with nearby context. Otherwise a
    # single click at the end of a recording can become an extra ASR sentence.
    i = 0
    while i < len(groups):
        start, end, voice_frames = groups[i]
        if voice_frames >= 10:
            i += 1
            continue
        before = start - groups[i - 1][1] if i else float('inf')
        after = groups[i + 1][0] - end if i + 1 < len(groups) else float('inf')
        if min(before, after) <= SAMPLE_RATE:
            if before <= after:
                previous = groups[i - 1]
                groups[i - 1] = previous[0], end, previous[2] + voice_frames
            else:
                following = groups[i + 1]
                groups[i + 1] = start, following[1], following[2] + voice_frames
            groups.pop(i)
        elif voice_frames < 4:
            groups.pop(i)
        else:
            i += 1
    groups = [(start, end) for start, end, _ in groups]
    spans = []
    maximum = int(max_seconds * SAMPLE_RATE)
    for i, (voice_start, voice_end) in enumerate(groups):
        left = (groups[i - 1][1] + voice_start) // 2 if i else 0
        right = (voice_end + groups[i + 1][0]) // 2 if i + 1 < len(groups) else len(values)
        start = max(left, voice_start - int(.24 * SAMPLE_RATE))
        end = min(right, voice_end + int(.16 * SAMPLE_RATE))
        while end - start > maximum:
            limit = start + maximum
            lo = math.ceil(max(start + 2 * SAMPLE_RATE, limit - 5 * SAMPLE_RATE) / frame)
            hi = limit // frame
            quiet = np.flatnonzero(rms[lo:hi] <= threshold)
            runs = np.split(quiet, np.flatnonzero(np.diff(quiet) > 1) + 1) if quiet.size else []
            candidates = [g for g in runs if len(g) >= 4]  # At least 80ms of quiet.
            if candidates:
                gap = candidates[-1]
                cut = int((lo + (int(gap[0]) + int(gap[-1]) + 1) / 2) * frame)
                reason = 'short_pause'
            else:
                local = rms[lo:hi]
                if len(local) >= 5 and float(local.max() - local.min()) > threshold * .1:
                    smooth = np.convolve(local, np.ones(5) / 5, mode='valid')
                    # Prefer the latest equivalent valley, rather than cutting
                    # all constant-volume input five seconds before the cap.
                    index = int(np.flatnonzero(smooth <= smooth.min() + 1e-7)[-1])
                    cut = (lo + index + 2) * frame
                else:
                    cut = limit
                reason = 'length'
            cut = max(start + frame, min(cut, limit))
            spans.append(AudioSpan(start, cut, reason))
            start = cut
        spans.append(AudioSpan(start, end, 'pause' if i + 1 < len(groups) else 'end'))
    return spans


def write_pcm(path: Path, pcm: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(pcm)


def decode_pcm(encoded: str) -> bytes:
    if len(encoded) > SAMPLE_RATE * 2 * MAX_CHUNK_SECONDS * 4 // 3 + 8:
        raise ValueError('音频片段超过 15 秒限制。')
    try:
        pcm = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError('音频编码无效。') from exc
    if not pcm or len(pcm) % 2 or len(pcm) > SAMPLE_RATE * 2 * MAX_CHUNK_SECONDS:
        raise ValueError('音频必须为不超过 15 秒的 16kHz 单声道 PCM16。')
    return pcm


def normalize_wav(blob: bytes):
    try:
        with wave.open(io.BytesIO(blob), 'rb') as source:
            if source.getsampwidth() != 2 or source.getcomptype() != 'NONE':
                raise ValueError('请上传未压缩的 16-bit PCM WAV 文件。')
            channels, rate, count = source.getnchannels(), source.getframerate(), source.getnframes()
            if channels not in (1, 2) or rate < 8000 or rate > 192000 or count / rate > 300:
                raise ValueError('支持最长 5 分钟的单声道／双声道 WAV，采样率 8–192kHz。')
            values = np.frombuffer(source.readframes(count), dtype='<i2').astype(np.float32)
            if values.size != count * channels or not values.size:
                raise ValueError('WAV 音频数据不完整或为空。')
    except (wave.Error, EOFError) as exc:
        raise ValueError('无法解析 WAV；请使用 16-bit PCM WAV 文件。') from exc
    if channels == 2:
        values = values.reshape(-1, 2).mean(axis=1)
    if rate != SAMPLE_RATE:
        # Polyphase anti-aliasing when available in model installs; linear fallback for development.
        try:
            from scipy.signal import resample_poly
            from math import gcd
            factor = gcd(rate, SAMPLE_RATE)
            values = resample_poly(values, SAMPLE_RATE // factor, rate // factor)
        except ImportError:
            values = np.interp(np.arange(round(len(values) * SAMPLE_RATE / rate)) * rate / SAMPLE_RATE,
                               np.arange(len(values)), values)
    return np.clip(values, -32768, 32767).astype('<i2').tobytes()
