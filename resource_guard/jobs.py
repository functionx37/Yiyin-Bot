"""Shared queue, bounded downloads and disposable worker supervision."""
import asyncio
import json
import logging
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from .admission import gate, ResourceBusy

log = logging.getLogger(__name__)
_queue = asyncio.Semaphore(1)
WORKER = Path(__file__).with_name('worker.py')


async def stop_process(process):
    if process.returncode is None:
        try: process.terminate()
        except ProcessLookupError: pass
        try: await asyncio.wait_for(process.wait(), 1)
        except asyncio.TimeoutError:
            try: process.kill()
            except ProcessLookupError: pass
            await process.wait()


class ImageJob:
    """Hold one global slot from preparation through file delivery."""
    async def __aenter__(self):
        gate.budget()
        try: await asyncio.wait_for(_queue.acquire(), 10)
        except asyncio.TimeoutError:
            raise ResourceBusy('当前有图片任务正在处理中，请稍后重试')
        try:
            self.budget = gate.budget()
            self.temporary = tempfile.TemporaryDirectory(prefix='yiyin_image_')
            self.directory = Path(self.temporary.name)
            self.input_size = 0
            self.input_count = 0
            return self
        except BaseException:
            _queue.release()
            raise

    async def __aexit__(self, *error):
        self.temporary.cleanup()
        _queue.release()

    def add_bytes(self, data):
        self.input_size += len(data)
        if self.input_size > self.budget // 4:
            raise ResourceBusy('图片输入超过当前资源预算，请稍后重试')
        path = self.directory / f'input-{self.input_count}'
        self.input_count += 1
        path.write_bytes(data)
        return path

    async def download(self, url):
        gate.budget()
        path = self.directory / f'input-{self.input_count}'
        self.input_count += 1
        async with httpx.AsyncClient(follow_redirects=True) as client:
            async with client.stream('GET',url,timeout=30) as response:
                response.raise_for_status()
                with path.open('wb') as target:
                    async for chunk in response.aiter_bytes(64*1024):
                        gate.budget()
                        self.input_size += len(chunk)
                        if self.input_size > self.budget // 4:
                            raise ResourceBusy('图片输入超过当前资源预算，请稍后重试')
                        target.write(chunk)
        return path

    def _encode(self, value):
        if isinstance(value, bytes): return {'__bytes_file__':str(self.add_bytes(value))}
        if isinstance(value, Path): return str(value)
        if isinstance(value, dict): return {key:self._encode(item) for key,item in value.items()}
        if isinstance(value, (tuple,list)): return [self._encode(item) for item in value]
        return value

    async def render(self, operation, payload, timeout=30):
        budget = min(self.budget,gate.budget())
        request = self.directory/'request.json'
        request.write_text(json.dumps({'operation':operation,'payload':self._encode(payload)},ensure_ascii=False))
        output = self.directory/'output'
        errors = self.directory/'error'
        started = time.monotonic()
        with errors.open('wb') as error_stream:
            process = await asyncio.create_subprocess_exec(sys.executable,str(WORKER),str(request),str(output),str(budget),
                stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.DEVNULL,stderr=error_stream)
            try:
                while process.returncode is None:
                    try: await asyncio.wait_for(process.wait(),0.25)
                    except asyncio.TimeoutError:
                        gate.budget()
                        if time.monotonic()-started >= timeout:
                            raise ResourceBusy('图片处理超时，已终止本次任务')
                if process.returncode != 0:
                    detail = errors.read_text(errors='replace')[:500].strip()
                    log.warning('image operation=%s code=%s detail=%s',operation,process.returncode,detail)
                    raise ResourceBusy(detail if process.returncode==2 and detail else '图片任务超过资源预算或处理失败，已安全终止')
                gate.budget()
                return output
            finally:
                await stop_process(process)


@asynccontextmanager
async def image_job(operation, payload):
    async with ImageJob() as job:
        output = await job.render(operation,payload)
        yield output


async def render_bytes(operation, payload):
    """For callers needing bytes, reserve room for transport copies before reading."""
    async with image_job(operation,payload) as output:
        if output.stat().st_size > gate.budget() // 4:
            raise ResourceBusy('生成图片超过当前发送内存预算，请稍后重试')
        return output.read_bytes()
