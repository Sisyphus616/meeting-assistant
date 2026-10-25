"""Coalesced rolling ASR previews; final clips remain the durable source."""
import asyncio
from dataclasses import dataclass
from uuid import UUID, uuid4

from .audio import write_pcm
from .language import recognition_source


@dataclass
class Preview:
    owner: object
    start: int
    latest: bytes
    task: object = None
    closed: bool = False
    cached: object = None


class LivePreviews:
    def __init__(self, runtime):
        self.runtime = runtime
        self.states = {}

    @staticmethod
    def key(mid, utterance):
        try:
            return mid, UUID(str(utterance)).hex
        except (ValueError, AttributeError):
            raise ValueError('无效的语音片段编号。') from None

    def accept(self, mid, utterance, pcm, start, owner):
        key = self.key(mid, utterance)
        state = self.states.get(key)
        if state is None:
            if sum(k[0] == mid for k in self.states) >= 4:
                return  # Preview work is optional; final audio is still accepted.
            state = self.states[key] = Preview(owner, start, pcm)
        if state.owner is not owner or state.closed:
            raise ValueError('该语音片段已提交，或属于另一个录音连接。')
        if start != state.start or len(pcm) < len(state.latest):
            raise ValueError('实时音频必须按顺序累计发送。')
        if not pcm.startswith(state.latest):
            raise ValueError('实时音频与已接收的内容不一致。')
        state.latest = pcm
        if state.task is None or state.task.done():
            state.task = self.runtime.spawn(mid, self.run(key, state))

    async def run(self, key, state):
        rt, (mid, utterance) = self.runtime, key
        processed = None
        while not state.closed and state.latest != processed:
            pcm = state.latest
            path = rt.settings.data_dir / 'previews' / mid / f'{uuid4().hex}.wav'
            write_pcm(path, pcm)
            try:
                meeting = rt.store.get(mid)
                text, elapsed, _ = await rt.queue.submit(0, '实时识别', rt.provider.transcribe,
                    path, recognition_source(meeting), meeting['glossary'])
                state.cached = pcm, text, elapsed
                if not state.closed:
                    await rt.send(mid, {'type': 'transcript_partial', 'utterance_id': utterance,
                        'text': text, 'start_ms': state.start,
                        'end_ms': state.start + len(pcm) // 32, 'asr_ms': elapsed})
            except Exception as exc:
                # A preview failure never turns into a final transcript or demo output.
                if not state.closed:
                    await rt.error(mid, exc)
            finally:
                path.unlink(missing_ok=True)
            processed = pcm

    def close(self, mid, utterance, pcm, start, owner):
        key = self.key(mid, utterance)
        state = self.states.get(key)
        if state is not None:
            if state.owner is not owner or state.closed:
                raise ValueError('该语音片段已提交，或属于另一个录音连接。')
            if state.start != start or not pcm.startswith(state.latest):
                raise ValueError('确认音频与已接收的实时音频不一致。')
            state.closed = True
        return key, state

    async def cached(self, state, pcm):
        if state is None:
            return None
        if state.task is not None:
            # Do not cancel a thread while it is reading its temporary WAV.
            await asyncio.shield(state.task)
        if state.cached and state.cached[0] == pcm:
            return state.cached[1:]
        return None

    def discard(self, key):
        self.states.pop(key, None)

    def snapshot(self, mid):
        return [{'utterance_id': key[1], 'start_ms': state.start,
                 'text': state.cached[1] if state.cached else '', 'pending': state.closed}
                for key, state in self.states.items() if key[0] == mid]

    def flush(self, mid):
        for key, state in list(self.states.items()):
            if key[0] == mid:
                self.disconnect(mid, state.owner)

    def disconnect(self, mid, owner):
        rt = self.runtime
        for key, state in list(self.states.items()):
            if key[0] != mid or state.owner is not owner or state.closed:
                continue
            state.closed = True
            path = rt.settings.data_dir / 'audio' / mid / f'{uuid4().hex}.wav'
            write_pcm(path, state.latest)
            rt.spawn(mid, rt.transcribe(mid, path, state.start,
                state.start + len(state.latest) // 32, acknowledge=False,
                preview=(key, state, state.latest)))
