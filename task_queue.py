"""Persistent local queue. Starting a task is atomic within the single service loop.
Interrupted model calls are never automatically resubmitted on restart.
"""
import asyncio
import uuid
import logging
import time
logger = logging.getLogger("ocr.processing")
from pathlib import Path
from datetime import datetime
from task_storage import TaskStatus


class TaskQueue:
    def __init__(self, storage, processor, max_concurrent_tasks=2, max_retries=0):
        self.storage = storage
        self.processor = processor
        self.max_concurrent_tasks = max(1, int(max_concurrent_tasks))
        self.queue = asyncio.Queue()
        self.active_tasks = {}
        self.is_running = False
        self.worker_task = None
        self.on_task_start = self.on_task_progress = None
        self.on_task_complete = self.on_task_error = None

    async def start(self):
        if self.is_running:
            return
        # Older versions called partially failed batches completed. Reconcile
        # their display status from stored page outcomes without running models.
        for task in self.storage.get_all_tasks(status=TaskStatus.COMPLETED):
            result = task.get('result_data') or {}
            pages = result.get('pages') or []
            failed = sum(p.get('status') == 'error' for p in pages)
            if failed:
                result['failed_pages'] = failed
                result['success_pages'] = sum(p.get('status') == 'success' for p in pages)
                self.storage.save_task_result(task['task_id'], result)
                self.storage.update_task_status(task['task_id'], TaskStatus.PARTIAL,
                    f'{failed} 页处理失败，可查看逐页原因')
        # A processing record may have already incurred remote charges. Keep its
        # checkpoints and request human inspection rather than replaying the batch.
        for task in self.storage.get_all_tasks(status=TaskStatus.PROCESSING):
            self.storage.update_task_status(task['task_id'], TaskStatus.UNKNOWN,
                '服务在处理期间中断。已保存完成页面；请核对平台调用记录，不会自动重新识别。')
        for task in self.storage.get_pending_tasks():
            await self.queue.put(task['task_id'])
        self.is_running = True
        self.worker_task = asyncio.create_task(self._worker())

    async def stop(self):
        self.is_running = False
        if self.worker_task:
            self.worker_task.cancel()
        running = list(self.active_tasks.values())
        for task in running:
            task.cancel()
        await asyncio.gather(*running, *([self.worker_task] if self.worker_task else []), return_exceptions=True)

    async def add_task(self, job_id, filename, image_paths, ocr_model, extract_model,
                       target_lang, ocr_system_prompt, auto_start=False, task_id=None):
        task_id = task_id or str(uuid.uuid4())
        existing = self.storage.get_task(task_id)
        if existing:
            return task_id
        self.storage.create_task(task_id, job_id, filename, len(image_paths), ocr_model,
            extract_model, target_lang, config={
                'image_paths': [str(p) for p in image_paths],
                'ocr_system_prompt': ocr_system_prompt})
        if auto_start:
            await self.queue.put(task_id)
        else:
            self.storage.update_task_status(task_id, TaskStatus.UPLOADED)
        return task_id

    async def start_task(self, task_id):
        task = self.storage.get_task(task_id)
        if not task:
            return False
        if task['status'] in ('pending', 'processing', 'completed', 'partial'):
            return True  # Repeated start does not create another queue entry.
        if task['status'] != 'uploaded':
            return False
        self.storage.update_task_status(task_id, TaskStatus.PENDING)
        await self.queue.put(task_id)
        return True

    async def retry_translation(self, task_id):
        task = self.storage.get_task(task_id)
        if not task:
            return False
        cfg = task.get('config') or {}
        if cfg.get('retry_stage') == 'translation' and task['status'] in ('pending', 'processing'):
            return True
        if task['status'] not in ('failed', 'partial', 'unknown'):
            return False
        pages = (task.get('result_data') or {}).get('pages', [])
        eligible = [p for p in pages if p.get('status') == 'error'
                    and (p.get('failed_stage') == 'translation' or str(p.get('error', '')).startswith('翻译失败'))
                    and (p.get('extracted_fields') or {}).get('fields')]
        if not eligible:
            return False
        cfg['retry_stage'] = 'translation'
        self.storage.update_config(task_id, cfg)
        self.storage.update_task_status(task_id, TaskStatus.PENDING)
        await self.queue.put(task_id)
        return True

    async def _retry_translation(self, task):
        task_id = task['task_id']
        result = task['result_data']
        try:
            for page in result['pages']:
                if page.get('status') != 'error' or not (
                    page.get('failed_stage') == 'translation' or str(page.get('error', '')).startswith('翻译失败')):
                    continue
                fields = (page.get('extracted_fields') or {}).get('fields')
                if not fields:
                    continue
                started = time.monotonic()
                try:
                    page['translations'] = await self.processor.extractor.translate_fields(
                        fields, target_lang=task['target_lang'], model=task['extract_model'])
                    page['status'] = 'success'
                    page.pop('error', None)
                    page.pop('failed_stage', None)
                    page.setdefault('timing', {})['translation_retry_seconds'] = round(time.monotonic()-started, 2)
                    logger.info('task=%s page=%s stage=translation_retry elapsed=%.2fs success', task_id, page['page_num'], time.monotonic()-started)
                except Exception as exc:
                    page.update(status='error', failed_stage='translation', error=str(exc))
                    logger.error('task=%s page=%s stage=translation_retry elapsed=%.2fs error=%s', task_id, page['page_num'], time.monotonic()-started, type(exc).__name__)
                self.storage.save_task_result(task_id, result)
            result['success_pages'] = sum(p.get('status') == 'success' for p in result['pages'])
            result['failed_pages'] = sum(p.get('status') != 'success' for p in result['pages'])
            self.storage.save_task_result(task_id, result)
            self.storage.update_task_progress(task_id, len(result['pages']))
            cfg = task.get('config') or {}
            cfg.pop('retry_stage', None)
            self.storage.update_config(task_id, cfg)
            final = TaskStatus.COMPLETED if not result['failed_pages'] else TaskStatus.PARTIAL if result['success_pages'] else TaskStatus.FAILED
            self.storage.update_task_status(task_id, final,
                f"{result['failed_pages']} 页翻译仍失败，可查看逐页原因" if result['failed_pages'] else None)
        except asyncio.CancelledError:
            self.storage.update_task_status(task_id, TaskStatus.UNKNOWN, '翻译重试期间服务中断，请核对；不会自动重发。')
            raise
        except Exception as exc:
            logger.exception('task=%s stage=translation_retry internal_error', task_id)
            self.storage.update_task_status(task_id, TaskStatus.FAILED, str(exc))

    async def cancel_task(self, task_id):
        task = self.storage.get_task(task_id)
        if not task or task['status'] not in ('uploaded', 'pending', 'paused'):
            return False
        self.storage.update_task_status(task_id, TaskStatus.CANCELLED)
        return True

    async def pause_task(self, task_id):
        task = self.storage.get_task(task_id)
        if not task or task['status'] != 'pending':
            return False
        self.storage.update_task_status(task_id, TaskStatus.PAUSED)
        return True

    async def resume_task(self, task_id):
        task = self.storage.get_task(task_id)
        if not task or task['status'] != 'paused':
            return False
        # A paused entry might still be in the queue. The worker's latest-state
        # check plus active-task guard makes duplicate entries harmless.
        self.storage.update_task_status(task_id, TaskStatus.PENDING)
        await self.queue.put(task_id)
        return True

    async def _worker(self):
        while self.is_running:
            if len(self.active_tasks) >= self.max_concurrent_tasks:
                await asyncio.sleep(.1)
                continue
            task_id = await self.queue.get()
            task = self.storage.get_task(task_id)
            if not task or task['status'] != 'pending' or task_id in self.active_tasks:
                self.queue.task_done()
                continue
            self.storage.update_task_status(task_id, TaskStatus.PROCESSING)
            running = asyncio.create_task(self._process_task(task))
            self.active_tasks[task_id] = running
            running.add_done_callback(lambda _, tid=task_id: self.active_tasks.pop(tid, None))
            self.queue.task_done()

    async def _process_task(self, task):
        task_id = task['task_id']
        if (task.get('config') or {}).get('retry_stage') == 'translation':
            await self._retry_translation(task)
            return
        pages = []
        def snapshot():
            ordered = sorted(pages, key=lambda p: p['page_num'])
            return {'job_id': task['job_id'], 'total_pages': task['total_pages'],
                    'success_pages': sum(p.get('status') == 'success' for p in ordered),
                    'failed_pages': sum(p.get('status') != 'success' for p in ordered),
                    'pages': ordered}
        async def checkpoint(page):
            pages.append(page)
            self.storage.save_task_result(task_id, snapshot())
            self.storage.update_task_progress(task_id, len(pages))
            if self.on_task_progress:
                await self.on_task_progress(task_id, {'processed_pages': len(pages)})
        try:
            if self.on_task_start:
                await self.on_task_start(task_id, task)
            cfg = task.get('config') or {}
            await self.processor.process_multiple_pages(
                [Path(p) for p in cfg.get('image_paths', [])], task['ocr_model'],
                task['extract_model'], cfg.get('ocr_system_prompt', ''),
                task['target_lang'], on_page=checkpoint)
            result = snapshot()
            result['completed_at'] = datetime.now().isoformat()
            self.storage.save_task_result(task_id, result)
            status = (TaskStatus.FAILED if not result['success_pages'] else
                      TaskStatus.PARTIAL if result['failed_pages'] else TaskStatus.COMPLETED)
            self.storage.update_task_status(task_id, status,
                f"{result['failed_pages']} 页处理失败，可查看逐页原因" if result['failed_pages'] else None)
            if self.on_task_complete:
                await self.on_task_complete(task_id, result)
        except asyncio.CancelledError:
            self.storage.update_task_status(task_id, TaskStatus.UNKNOWN,
                '服务停止时任务尚未结束。已保存完成页面，请核对后再决定是否新建任务。')
            raise
        except Exception as exc:
            self.storage.update_task_status(task_id, TaskStatus.FAILED, str(exc))
            if self.on_task_error:
                await self.on_task_error(task_id, str(exc))

    async def get_task_info(self, task_id):
        return self.storage.get_task(task_id)

    async def get_all_tasks(self, limit=50):
        return self.storage.get_all_tasks(limit=limit)

    async def get_queue_status(self):
        return {'is_running': self.is_running, 'queue_size': self.queue.qsize(),
                'active_tasks': len(self.active_tasks), 'max_concurrent': self.max_concurrent_tasks,
                'stats': self.storage.get_queue_stats()}
