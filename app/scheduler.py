"""Non-preemptive priority scheduler; each generate call is a bounded work unit."""
import asyncio
import itertools
import time
from concurrent.futures import ThreadPoolExecutor


class InferenceQueue:
    def __init__(self, max_pending=24):
        self.queue = asyncio.PriorityQueue(maxsize=max_pending)
        self.counter = itertools.count()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='inference')
        self.worker = None
        self.active = None
        self.completed = 0
        self.samples = []

    def start(self):
        self.worker = asyncio.create_task(self.run())

    async def submit(self, priority, kind, fn, *args):
        if self.queue.full():
            raise RuntimeError('推理队列已满，请暂停输入，待现有任务完成后重试。')
        future = asyncio.get_running_loop().create_future()
        self.queue.put_nowait((priority, next(self.counter), kind, fn, args, future, time.perf_counter()))
        return await future

    async def run(self):
        while True:
            _, _, kind, fn, args, future, queued = await self.queue.get()
            if future.cancelled():
                self.queue.task_done()
                continue
            started = time.perf_counter()
            self.active = kind
            try:
                value = await asyncio.get_running_loop().run_in_executor(self.executor, fn, *args)
                elapsed = (time.perf_counter() - started) * 1000
                waited = (started - queued) * 1000
                if not future.done():
                    future.set_result((value, elapsed, waited))
                self.completed += 1
                self.samples.append({'kind': kind, 'inference_ms': round(elapsed, 1), 'queue_ms': round(waited, 1)})
                self.samples = self.samples[-100:]
            except Exception as exc:
                if not future.done():
                    future.set_exception(exc)
            finally:
                self.active = None
                self.queue.task_done()

    async def close(self):
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        while not self.queue.empty():
            *_, future, queued = self.queue.get_nowait()
            future.cancel()
            self.queue.task_done()
        self.executor.shutdown(wait=False, cancel_futures=True)

    def metrics(self):
        return {'pending': self.queue.qsize(), 'active': self.active,
                'completed': self.completed, 'samples': self.samples[-20:]}
