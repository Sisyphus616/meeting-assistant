import base64
import binascii
import io
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
MAX_CHUNK_SECONDS = 15


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
