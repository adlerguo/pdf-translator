"""Isolated contract tests: never use real files, task DB, or model requests."""
import asyncio
import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_SANDBOX = tempfile.TemporaryDirectory()
root = Path(_SANDBOX.name)
for key, child in [('OCR_UPLOAD_DIR','uploads'),('OCR_OUTPUT_DIR','outputs'),('OCR_DATA_DIR','data')]:
    os.environ[key] = str(root / child)
try:
    import httpx
except ImportError:
    import httpx2 as httpx
from PIL import Image
import app_v2 as api
from task_storage import TaskStorage, TaskStatus
from task_queue import TaskQueue


class FakeProcessor:
    def __init__(self):
        self.calls = 0
        self.gate = asyncio.Event()
        self.gate.set()
    async def process_multiple_pages(self, paths, *args, on_page=None):
        self.calls += 1
        await self.gate.wait()
        results=[]
        for i, _ in enumerate(paths, 1):
            page = {'page_num': i, 'status': 'success' if i == 1 else 'error',
                    'ocr_text': '测试原文', 'doc_type_cn': '驾驶证',
                    'extracted_fields': {'fields': {'name': {'value': '测试', 'field_type': '姓名'}}},
                    'translations': {'name': 'Test'}}
            if i > 1: page['error'] = '模拟单页失败'
            results.append(page)
            if on_page: await on_page(page)
        return results


class WorkbenchAPI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.TemporaryDirectory(dir=root)
        self.storage = TaskStorage(Path(self.dir.name) / 'tasks.db')
        self.fake = FakeProcessor()
        self.queue = TaskQueue(self.storage, self.fake)
        api.task_storage, api.task_queue = self.storage, self.queue
        await self.queue.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url='http://test')
    async def asyncTearDown(self):
        await self.queue.stop()
        await self.client.aclose()
        self.dir.cleanup()
    async def asset(self):
        buf=io.BytesIO();Image.new('RGB',(40,30),'white').save(buf,'PNG')
        response=await self.client.post('/api/upload',files={'file':('test.png',buf.getvalue(),'image/png')})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()
    def payload(self, asset, request_id='test-request-0001'):
        return {'job_id':asset['job_id'],'filename':asset['filename'],'request_id':request_id,
                'ocr_model':api.config['models']['ocr']['name'],
                'extract_model':api.config['models']['translation']['name'],
                'target_lang':'en','auto_start':'true'}
    async def settle(self, task_id):
        for _ in range(100):
            task=self.storage.get_task(task_id)
            if task['status'] in ('completed','partial','failed'): return task
            await asyncio.sleep(.01)
        self.fail('Queue did not finish')
    async def test_upload_validation_and_recovery(self):
        a=await self.asset()
        r=await self.client.get('/api/assets/'+a['job_id']);self.assertEqual(r.json()['total_pages'],1)
        r=await self.client.get('/api/image/'+a['job_id']+'/1');self.assertEqual(r.status_code,200)
        for name,content in [('bad.txt',b'hello'),('bad.png',b'not an image'),('empty.pdf',b'')]:
            r=await self.client.post('/api/upload',files={'file':(name,content)})
            self.assertEqual(r.status_code,400,r.text)
        previous=api.MAX_UPLOAD_BYTES;api.MAX_UPLOAD_BYTES=5
        try:
            r=await self.client.post('/api/upload',files={'file':('big.png',b'123456')})
            self.assertEqual(r.status_code,413)
        finally: api.MAX_UPLOAD_BYTES=previous
    async def test_idempotency_persistence_and_config_conflict(self):
        a=await self.asset();payload=self.payload(a)
        results=await asyncio.gather(*(self.client.post('/api/tasks/add',data=payload) for _ in range(4)))
        self.assertTrue(all(r.status_code==200 for r in results))
        task_id=results[0].json()['task_id'];await self.settle(task_id)
        self.assertEqual(len(self.storage.get_all_tasks()),1);self.assertEqual(self.fake.calls,1)
        payload['target_lang']='ja'
        r=await self.client.post('/api/tasks/add',data=payload);self.assertEqual(r.status_code,409)
        reopened=TaskStorage(self.storage.db_path);self.assertEqual(reopened.get_task(task_id)['status'],'completed')
        r=await self.client.get('/api/tasks/list');self.assertNotIn('result_data',r.json()['tasks'][0])
    async def test_partial_progress_numeric_order_review_export_hide(self):
        a=await self.asset();job=api.UPLOAD_DIR/a['job_id']
        for number in range(2,13):Image.new('RGB',(10,10),'white').save(job/f'page_{number}.png')
        payload=self.payload(a,'test-request-0012');r=await self.client.post('/api/tasks/add',data=payload)
        task_id=r.json()['task_id'];task=await self.settle(task_id)
        self.assertEqual(task['status'],'partial');self.assertEqual(task['processed_pages'],12)
        self.assertEqual([Path(p).stem for p in task['config']['image_paths']],['page_'+str(i) for i in range(1,13)])
        pages=task['result_data']['pages'];pages[0]['translations']['name']='Edited'
        r=await self.client.put(f'/api/tasks/{task_id}/result',json={'pages':pages});self.assertEqual(r.status_code,200,r.text)
        r=await self.client.get(f'/api/tasks/{task_id}/result');self.assertEqual(r.json()['pages'][0]['translations']['name'],'Edited')
        bad=await self.client.put(f'/api/tasks/{task_id}/result',json={'pages':[]});self.assertEqual(bad.status_code,400)
        for endpoint in ['/api/export/merged','/api/export/original']:
            r=await self.client.post(endpoint,json={'job_id':a['job_id'],'pages':pages,'target_lang':'en'})
            self.assertEqual(r.status_code,200,r.text if r.status_code!=200 else '')
            with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
                xml=archive.read('word/document.xml').decode()
                self.assertIn('Edited' if endpoint.endswith('merged') else '测试原文',xml)
        r=await self.client.post(f'/api/tasks/{task_id}/visibility',json={'hidden':True});self.assertEqual(r.status_code,200)
        self.assertTrue(self.storage.get_task(task_id)['hidden'])
        await self.client.post(f'/api/tasks/{task_id}/visibility',json={'hidden':False})
        self.assertFalse(self.storage.get_task(task_id)['hidden'])
        r=await self.client.delete(f'/api/tasks/{task_id}');self.assertEqual(r.status_code,409)
    async def test_repeat_start_and_interruption_not_replayed(self):
        a=await self.asset();payload=self.payload(a);payload['auto_start']='false'
        r=await self.client.post('/api/tasks/add',data=payload);tid=r.json()['task_id']
        self.fake.gate.clear()
        for _ in range(3):self.assertEqual((await self.client.post(f'/api/tasks/{tid}/start')).status_code,200)
        await asyncio.sleep(.05);self.assertEqual(self.fake.calls,1)
        # The API loop remains available while the model processor is waiting.
        r=await asyncio.wait_for(self.client.get('/api/tasks/list'),.5);self.assertEqual(r.status_code,200)
        await self.queue.stop();self.assertEqual(self.storage.get_task(tid)['status'],'unknown')
        new=TaskQueue(self.storage,self.fake);await new.start();await asyncio.sleep(.02)
        self.assertEqual(self.fake.calls,1);await new.stop()
    async def test_real_pdf_conversion_and_order(self):
        # Minimal synthetic PDF: no user material and no external dependencies.
        objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
                   b'<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>',
                   b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 180 240] /Resources << >> >>',
                   b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 180 240] /Resources << >> >>']
        pdf=b'%PDF-1.4\n'; offsets=[0]
        for number,obj in enumerate(objects,1):
            offsets.append(len(pdf));pdf+=str(number).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
        xref=len(pdf);pdf+=b'xref\n0 5\n0000000000 65535 f \n'
        for offset in offsets[1:]:pdf+=f'{offset:010d} 00000 n \n'.encode()
        pdf+=f'trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()
        r=await self.client.post('/api/upload',files={'file':('two-pages.pdf',pdf,'application/pdf')})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['total_pages'],2)
        image=await self.client.get('/api/image/'+r.json()['job_id']+'/2')
        self.assertEqual(image.status_code,200)

    async def test_async_model_pipeline(self):
        from unittest.mock import AsyncMock
        from types import SimpleNamespace
        from multi_page_processor import MultiPageProcessor
        processor=MultiPageProcessor('test-key','http://127.0.0.1:1')
        image=Path(self.dir.name)/'page.png';Image.new('RGB',(30,30),'white').save(image)
        response=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='test text'))])
        processor.client.chat.completions.create=AsyncMock(return_value=response)
        processor.extractor.detect_document_type=AsyncMock(return_value={'type':'other','type_cn':'测试'})
        processor.extractor.extract_fields_intelligently=AsyncMock(return_value={'fields':{'name':{'value':'Test'}}})
        processor.extractor.translate_fields=AsyncMock(return_value={'name':'Test'})
        pages=await processor.process_multiple_pages([image],'test','test','read this')
        self.assertEqual(pages[0]['status'],'success')
        processor.client.chat.completions.create.assert_awaited_once()
        processor.extractor.translate_fields.assert_awaited_once()
        await processor.client.close();await processor.extractor.client.close()

    async def test_legacy_partial_status_without_reprocessing(self):
        a=await self.asset()
        self.storage.create_task('legacy-partial',a['job_id'],'legacy.pdf',2,'ocr','extract','en')
        self.storage.save_task_result('legacy-partial',{'pages':[{'page_num':1,'status':'success'},{'page_num':2,'status':'error'}]})
        self.storage.update_task_status('legacy-partial',TaskStatus.COMPLETED)
        await self.queue.stop()
        new=TaskQueue(self.storage,self.fake);await new.start()
        task=self.storage.get_task('legacy-partial')
        self.assertEqual(task['status'],'partial');self.assertEqual(task['result_data']['failed_pages'],1)
        self.assertEqual(self.fake.calls,0);await new.stop()

    async def test_translation_only_retry_reuses_fields(self):
        from unittest.mock import AsyncMock
        from types import SimpleNamespace
        a=await self.asset()
        self.storage.create_task('translation-timeout',a['job_id'],'test.png',1,'ocr','extract','en')
        self.storage.save_task_result('translation-timeout',{'total_pages':1,'job_id':a['job_id'],
            'pages':[{'page_num':1,'status':'error','error':'翻译失败：Request timed out.',
                      'ocr_text':'preserved source','extracted_fields':{'fields':{'name':{'value':'Original'}}}}]})
        self.storage.update_task_status('translation-timeout',TaskStatus.FAILED)
        translate=AsyncMock(return_value={'name':'Translated'})
        self.fake.extractor=SimpleNamespace(translate_fields=translate)
        listing=await self.client.get('/api/tasks/list')
        self.assertTrue(listing.json()['tasks'][0]['retry_translation_available'])
        replies=await asyncio.gather(*(self.client.post('/api/tasks/translation-timeout/retry-translation') for _ in range(2)))
        self.assertTrue(all(r.status_code==200 for r in replies))
        result=await self.settle('translation-timeout')
        self.assertEqual(result['status'],'completed');self.assertEqual(self.fake.calls,0)
        translate.assert_awaited_once()
        self.assertEqual(result['result_data']['pages'][0]['ocr_text'],'preserved source')
        self.assertEqual(result['result_data']['pages'][0]['translations']['name'],'Translated')

    async def test_model_timeout_config(self):
        from model_client import create_async_client
        client=create_async_client('test-key','http://127.0.0.1:1')
        self.assertEqual(client.timeout.read,300)
        self.assertEqual(client.timeout.connect,20)
        self.assertEqual(client.max_retries,0)
        await client.close()

    async def test_backend_validation_and_missing_task(self):
        a=await self.asset();payload=self.payload(a);payload['ocr_model']='not-configured'
        r=await self.client.post('/api/tasks/add',data=payload);self.assertEqual(r.status_code,400)
        r=await self.client.get('/api/tasks/unknown-id');self.assertEqual(r.status_code,404)
        self.assertEqual(len(self.storage.get_all_tasks()),0)

if __name__=='__main__':unittest.main()
