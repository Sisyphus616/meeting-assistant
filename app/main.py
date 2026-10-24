import asyncio
import json
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Literal

from .audio import SAMPLE_RATE, decode_pcm, normalize_wav, write_pcm
from .config import ROOT, Settings
from .providers import DEMO, make_provider
from .scheduler import InferenceQueue
from .store import Store

log = logging.getLogger('meeting-assistant')


class MeetingInput(BaseModel):
    title: str = Field(default='未命名课堂', min_length=1, max_length=100)
    source: Literal['zh', 'en', 'auto'] = 'zh'
    target: Literal['zh', 'en'] = 'en'
    glossary: str = Field(default='ROCm, Qwen, AMD, GPU, 语音活动检测', max_length=2000)


class Runtime:
    def __init__(self, settings, provider=None):
        self.settings = settings
        self.store = Store(settings.data_dir)
        self.provider = provider or make_provider(settings)
        self.queue = InferenceQueue(settings.max_pending)
        self.clients = defaultdict(dict)
        self.pending = defaultdict(set)
        self.summary_running = set()
        self.retranslating = set()
        self.demo_index = defaultdict(int)
        self.finish_tasks = {}

    async def send(self, mid, event):
        for socket, lock in list(self.clients[mid].items()):
            try:
                async with lock:
                    await socket.send_json(event)
            except (RuntimeError, WebSocketDisconnect, OSError):
                self.clients[mid].pop(socket, None)

    async def snapshot(self, mid):
        await self.send(mid, {'type': 'snapshot', 'meeting': self.store.get(mid),
                              'metrics': self.queue.metrics(),
                              'retranslating': mid in self.retranslating,
                              'summary_running': mid in self.summary_running})

    async def error(self, mid, exc):
        log.warning('Meeting %s: %s', mid, exc)
        await self.send(mid, {'type': 'error', 'message': str(exc)[:600]})

    def spawn(self, mid, coroutine):
        task = asyncio.create_task(coroutine)
        self.pending[mid].add(task)
        task.add_done_callback(self.pending[mid].discard)
        return task

    def writable(self, mid):
        meeting = self.store.get(mid)
        if not meeting:
            raise ValueError('会议不存在。')
        if meeting['status'] != 'live':
            raise ValueError('当前会议已结束或正在结束，请新建会议。')
        if len(self.pending[mid]) >= self.settings.max_pending:
            raise ValueError('处理任务已积压，请等待后重试。')
        return meeting

    async def translate(self, mid, segment):
        try:
            meeting = self.store.get(mid)
            prior = [s['text'] for s in meeting['segments'] if s['id'] < segment['id']][-2:]
            text, elapsed, _ = await self.queue.submit(1, '翻译', self.provider.translate,
                segment['text'], meeting['target'], '\n'.join(prior), meeting['glossary'])
            self.store.translate(segment['id'], text, elapsed)
        except Exception as exc:
            self.store.translate(segment['id'], '', None, str(exc)[:600])
            await self.error(mid, exc)
        await self.snapshot(mid)
        self.maybe_summary(mid)

    async def transcribe(self, mid, path, start, end, acknowledge=True):
        try:
            meeting = self.store.get(mid)
            text, elapsed, _ = await self.queue.submit(0, '识别', self.provider.transcribe,
                path, meeting['source'], meeting['glossary'])
            if not text.strip():
                await self.send(mid, {'type': 'notice', 'message': '该片段未识别到文字，录音已保留。'})
                return
            segment = self.store.segment(mid, text, start, end, elapsed, str(path.relative_to(self.settings.data_dir)))
            await self.snapshot(mid)
            await self.translate(mid, segment)
        except Exception as exc:
            await self.error(mid, exc)
        finally:
            if acknowledge:
                await self.send(mid, {'type': 'audio_done'})

    def unsummarized(self, mid):
        meeting = self.store.get(mid)
        included = {sid for s in meeting['summaries'] for sid in s['segment_ids']}
        return [s for s in meeting['segments'] if s['id'] not in included]

    def maybe_summary(self, mid):
        if (mid not in self.summary_running and self.store.get(mid)['status'] == 'live'
                and len(self.unsummarized(mid)) >= self.settings.summary_every):
            self.summary_running.add(mid)
            self.spawn(mid, self.summarize(mid))

    async def summarize(self, mid):
        try:
            segments = self.unsummarized(mid)[:8]
            if not segments:
                return
            await self.snapshot(mid)
            meeting = self.store.get(mid)
            content, elapsed, _ = await self.queue.submit(2, '纪要', self.provider.summarize, segments, meeting['glossary'])
            self.store.summary(mid, content, [s['id'] for s in segments], elapsed)
        except Exception as exc:
            await self.error(mid, exc)
        finally:
            self.summary_running.discard(mid)
            await self.snapshot(mid)
        # Another bounded block may have arrived during inference.
        # Failed summaries are retried only by explicit user action or new input.

    async def finish(self, mid):
        try:
            tasks = list(self.pending[mid])
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            while self.unsummarized(mid):
                count = len(self.unsummarized(mid))
                self.summary_running.add(mid)
                await self.summarize(mid)
                if len(self.unsummarized(mid)) >= count:
                    break
            self.store.status(mid, 'completed')
            await self.snapshot(mid)
            await self.send(mid, {'type': 'finished'})
        except Exception as exc:
            await self.error(mid, exc)
        finally:
            self.finish_tasks.pop(mid, None)

    async def handle(self, mid, message):
        kind = message.get('type')
        if kind == 'ping':
            await self.send(mid, {'type': 'metrics', 'metrics': self.queue.metrics()})
            return
        if kind == 'retranslate':
            meeting = self.store.get(mid)
            if self.provider.name == 'demo' or meeting['provider'] != self.provider.name:
                raise ValueError('重译需要在模型模式中打开一条模型记录。')
            if not meeting['segments'] or meeting['status'] == 'finishing' or self.pending[mid]:
                raise ValueError('没有可重译的原文，或当前仍有任务在处理。')
            async def retranslate():
                await self.send(mid, {'type': 'retranslation_started'})
                try:
                    for segment in meeting['segments']:
                        await self.translate(mid, segment)
                finally:
                    self.retranslating.discard(mid)
                    await self.snapshot(mid)
                    await self.send(mid, {'type': 'retranslation_done'})
            self.retranslating.add(mid)
            self.spawn(mid, retranslate())
            return
        if kind == 'summary':
            meeting = self.store.get(mid)
            if meeting['provider'] != self.provider.name:
                raise ValueError('请在这条记录原先的运行模式下整理纪要，或新建一条记录。')
            if meeting['status'] == 'finishing':
                raise ValueError('正在结束会议，剩余纪要将自动生成。')
            if mid in self.summary_running:
                raise ValueError('纪要正在生成，请稍候。')
            if not self.unsummarized(mid):
                raise ValueError('当前没有尚未整理的原文。')
            self.summary_running.add(mid)
            self.spawn(mid, self.summarize(mid))
            return
        if kind == 'finish':
            self.writable(mid)
            self.store.status(mid, 'finishing')
            self.finish_tasks[mid] = asyncio.create_task(self.finish(mid))
            await self.snapshot(mid)
            return
        meeting = self.writable(mid)
        if kind in ('text', 'demo_next'):
            if kind == 'demo_next':
                if self.provider.name != 'demo':
                    raise ValueError('真实模型模式不能使用预设演示转写。')
                index = self.demo_index[mid]
                if index >= len(DEMO):
                    await self.send(mid, {'type': 'demo_done'})
                    return
                self.demo_index[mid] += 1
                text = DEMO[index][1 if meeting['source'] == 'en' else 0]
                start, end = index * 8000, (index + 1) * 8000
            else:
                text = str(message.get('text', '')).strip()
                if not text or len(text) > 2000:
                    raise ValueError('每次输入需为 1–2000 字。')
                start = max(0, int(message.get('start_ms', 0)))
                end = max(start, int(message.get('end_ms', start)))
            segment = self.store.segment(mid, text, start, end)
            await self.snapshot(mid)
            self.spawn(mid, self.translate(mid, segment))
        elif kind == 'audio':
            if self.provider.name == 'demo':
                raise ValueError('演示模式不进行语音识别。请配置 Qwen 后录音。')
            if message.get('sample_rate') != SAMPLE_RATE:
                raise ValueError('需要 16kHz PCM 音频。')
            pcm = decode_pcm(message.get('pcm16', ''))
            start = max(0, int(message.get('start_ms', 0)))
            end = start + round(len(pcm) / 32)
            path = self.settings.data_dir / 'audio' / mid / f'{uuid4().hex}.wav'
            write_pcm(path, pcm)
            self.spawn(mid, self.transcribe(mid, path, start, end))
        else:
            raise ValueError('未知消息类型。')


def create_app(settings=None, provider=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        runtime = Runtime(settings, provider)
        app.state.runtime = runtime
        runtime.queue.start()
        yield
        tasks = [task for pending in runtime.pending.values() for task in pending] + list(runtime.finish_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await runtime.queue.close()
        if hasattr(runtime.provider, 'close'):
            await asyncio.to_thread(runtime.provider.close)

    app = FastAPI(title='语音转写与翻译', lifespan=lifespan, root_path=settings.root_path)

    def runtime(request):
        return request.app.state.runtime

    def require_meeting(rt, mid):
        meeting = rt.store.get(mid)
        if not meeting:
            raise HTTPException(404, '会议不存在')
        return meeting

    @app.get('/api/health')
    async def health(request: Request):
        rt = runtime(request)
        return {'status': 'ok', 'provider': rt.provider.name, 'device': settings.device,
                'asr_model': settings.asr_model, 'llm_model': settings.llm_model,
                'translation_model': settings.translation_model,
                'translation_backend': settings.translation_backend,
                'summary_every': settings.summary_every,
                'summary_style': settings.summary_style,
                'metrics': rt.queue.metrics(), 'demo_sentences': len(DEMO),
                'inference_mode': 'utterance-chunked', 'models_loaded': {
                    'asr': getattr(rt.provider, 'asr', None) is not None,
                    'llm': getattr(rt.provider, 'llm', None) is not None,
                    'translation': (getattr(getattr(rt.provider, 'translator', None), 'model', None) is not None or
                        (getattr(getattr(rt.provider, 'translator', None), 'process', None) is not None and
                         rt.provider.translator.process.poll() is None))}}

    @app.get('/api/meetings')
    async def meetings(request: Request):
        return runtime(request).store.list()

    @app.post('/api/meetings', status_code=201)
    async def create(body: MeetingInput, request: Request):
        rt = runtime(request)
        return rt.store.create(body.title.strip() or '未命名课堂', body.source, body.target, body.glossary, rt.provider.name)

    @app.get('/api/meetings/{mid}')
    async def detail(mid: str, request: Request):
        return require_meeting(runtime(request), mid)

    @app.get('/api/meetings/{mid}/audio/{sid}')
    async def audio(mid: str, sid: int, request: Request):
        meeting = require_meeting(runtime(request), mid)
        segment = next((s for s in meeting['segments'] if s['id'] == sid), None)
        if not segment or not segment['audio_path']:
            raise HTTPException(404, '该段没有录音')
        path = settings.data_dir / segment['audio_path']
        if not path.is_file():
            raise HTTPException(404, '录音文件缺失')
        return FileResponse(path, media_type='audio/wav')

    @app.get('/api/meetings/{mid}/export')
    async def export(mid: str, request: Request, format: Literal['md', 'json', 'srt'] = 'md'):
        meeting = require_meeting(runtime(request), mid)
        if format == 'json':
            content, media = json.dumps(meeting, ensure_ascii=False, indent=2), 'application/json'
        elif format == 'srt':
            def stamp(ms):
                hours, rest = divmod(ms, 3600000)
                minutes, rest = divmod(rest, 60000)
                seconds, millis = divmod(rest, 1000)
                return f'{hours:02}:{minutes:02}:{seconds:02},{millis:03}'
            content = '\n\n'.join(f'{i}\n{stamp(s["start_ms"])} --> {stamp(max(s["end_ms"], s["start_ms"] + 1000))}\n{s["text"]}\n{s["translation"]}'
                for i, s in enumerate(meeting['segments'], 1))
            media = 'application/x-subrip'
        else:
            lines = [f'# {meeting["title"]}', '', f'创建时间：{meeting["created_at"]}',
                     f'模式：{meeting["provider"]}', '', '## 分段纪要', '']
            for i, s in enumerate(meeting['summaries'], 1):
                lines += [f'### 段落 {i}', '', s['content'], '',
                          '原文片段：' + ', '.join(str(n) for n in s['segment_ids']), '']
            lines += ['## 双语原文', '']
            for s in meeting['segments']:
                lines += [f'### [{s["id"]}] {s["start_ms"] / 1000:.1f}s', '', s['text'], '', s['translation'] or '（翻译未完成）', '']
            content, media = '\n'.join(lines), 'text/markdown'
        return Response(content, media_type=media,
            headers={'Content-Disposition': f'attachment; filename="meeting-{mid[:8]}.{format}"'})

    @app.post('/api/meetings/{mid}/upload')
    async def upload(mid: str, request: Request, file: UploadFile):
        rt = runtime(request)
        require_meeting(rt, mid)
        try:
            rt.writable(mid)
            if rt.provider.name == 'demo':
                raise ValueError('演示模式不识别录音，请切换 Qwen 模式。')
            blob = await file.read(20 * 1024 * 1024 + 1)
            if len(blob) > 20 * 1024 * 1024:
                raise ValueError('文件不能超过 20MB。')
            pcm = await asyncio.to_thread(normalize_wav, blob)
            if rt.pending[mid]:
                raise ValueError('请等待当前任务完成后再导入录音。')
            async def process_file():
                offset = max((s['end_ms'] for s in rt.store.get(mid)['segments']), default=0)
                step = SAMPLE_RATE * 2 * 10
                for index in range(0, len(pcm), step):
                    chunk = pcm[index:index + step]
                    path = settings.data_dir / 'audio' / mid / f'{uuid4().hex}.wav'
                    write_pcm(path, chunk)
                    start = offset + index // 32
                    await rt.transcribe(mid, path, start, start + len(chunk) // 32, acknowledge=False)
            # Recheck after awaiting file reads so a concurrent finish cannot accept new input.
            rt.writable(mid)
            rt.spawn(mid, process_file())
            return {'accepted': True, 'duration_ms': len(pcm) // 32}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.websocket('/ws/{mid}')
    async def socket(socket: WebSocket, mid: str):
        rt = socket.app.state.runtime
        await socket.accept()
        if not rt.store.get(mid):
            await socket.close(code=1008, reason='会议不存在')
            return
        rt.clients[mid][socket] = asyncio.Lock()
        try:
            await rt.snapshot(mid)
            while True:
                raw = await socket.receive_text()
                if len(raw) > 700000:
                    await socket.close(code=1009, reason='消息过大')
                    break
                message = None
                try:
                    message = json.loads(raw)
                    if not isinstance(message, dict):
                        raise ValueError('消息需要是 JSON 对象。')
                    await rt.handle(mid, message)
                except (ValueError, TypeError, KeyError, RuntimeError) as exc:
                    await rt.error(mid, exc)
                    if isinstance(message, dict) and message.get('type') == 'audio':
                        await rt.send(mid, {'type': 'audio_done'})
        except WebSocketDisconnect:
            pass
        finally:
            rt.clients[mid].pop(socket, None)

    app.mount('/static', StaticFiles(directory=ROOT / 'web'), name='static')

    @app.get('/')
    async def index():
        return FileResponse(ROOT / 'web' / 'index.html', headers={'Cache-Control': 'no-cache'})

    return app


app = create_app()
