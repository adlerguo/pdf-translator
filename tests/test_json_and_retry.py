"""Targeted connection and model JSON tests. No live API calls."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import openai
try:
    import httpx2 as httpx
except ImportError:
    import httpx
from json_parser import parse_json_safe, log_response_metadata
from intelligent_extractor import IntelligentExtractor
from multi_page_processor import MultiPageProcessor


def response(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class JSONParserTest(unittest.TestCase):
    def test_comments_preserve_urls_comment_literals_and_quotes(self):
        original={'url':'https://example.com/a//b','literal':'/* is data */ // still data',
                  'quoted':'He said "hello"','path':'C:\\docs\\sample'}
        text=json.dumps(original,ensure_ascii=False)
        text='// header\n'+text[:-1]+', /* inline */ "flag": true}\n// footer'
        parsed=parse_json_safe(text,'comments')
        self.assertEqual(parsed,{**original,'flag':True})
    def test_literal_controls_preserve_field_values(self):
        text='```JSON\n{"value":"第一行\n第二行\t末尾\r\x00","escaped":"a\\nb"}\n```'
        with tempfile.TemporaryDirectory() as tmp, patch('tempfile.tempdir',tmp):
            result=parse_json_safe(text,'controls')
            self.assertEqual(result['value'],'第一行\n第二行\t末尾\r\x00')
            self.assertEqual(result['escaped'],'a\nb')
            self.assertEqual(list(Path(tmp).iterdir()),[])
    def test_unrecoverable_response_has_private_unique_diagnostic(self):
        text='{"name":"truncated'
        with tempfile.TemporaryDirectory() as tmp,patch('tempfile.tempdir',tmp):
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError,'诊断文件已保存'):
                    parse_json_safe(text,'../../translate_fields')
            files=list(Path(tmp).glob('json_fail_*.txt'))
            self.assertEqual(len(files),2)
            for file in files:
                self.assertIn(text,file.read_text())
                if os.name!='nt':self.assertEqual(file.stat().st_mode & 0o777,0o600)
    def test_invalid_top_level_and_unterminated_comment_are_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp,patch('tempfile.tempdir',tmp):
            for text in ['[1,2]','{"a":1} /* incomplete','{"a":1,}']:
                with self.assertRaises(RuntimeError):parse_json_safe(text,'invalid')

    def test_truncation_hint_and_response_metadata_saved(self):
        metadata={'finish_reason':'length','max_tokens':8192,'usage':{'completion_tokens':8192}}
        with tempfile.TemporaryDirectory() as tmp,patch('tempfile.tempdir',tmp):
            with self.assertRaisesRegex(RuntimeError,'达到长度限制'):
                parse_json_safe('{"value":"unfinished','translate_fields',response_metadata=metadata)
            diagnostic=next(Path(tmp).glob('json_fail_*.txt')).read_text()
            self.assertIn('"finish_reason": "length"',diagnostic)
            self.assertIn('"completion_tokens": 8192',diagnostic)
    def test_truncation_is_only_suspected_without_length_finish_reason(self):
        with tempfile.TemporaryDirectory() as tmp,patch('tempfile.tempdir',tmp):
            with self.assertRaisesRegex(RuntimeError,'疑似截断') as caught:
                parse_json_safe('{"value":"unfinished','translate_fields',response_metadata={'finish_reason':'stop'})
            self.assertNotIn('已达到长度限制',str(caught.exception))
            with self.assertRaises(RuntimeError) as other:
                parse_json_safe('{"value":?}', 'invalid')
            self.assertNotIn('疑似截断',str(other.exception))
    def test_response_metadata_contains_finish_and_usage_but_not_content(self):
        data=SimpleNamespace(id='response-test',choices=[SimpleNamespace(finish_reason='length',message=SimpleNamespace(content='private document content'))],
            usage=SimpleNamespace(prompt_tokens=100,completion_tokens=8192,total_tokens=8292,
                                  completion_tokens_details=SimpleNamespace(reasoning_tokens=400)))
        with self.assertLogs('ocr.processing',level='INFO') as logs:
            metadata=log_response_metadata(data,'translate_fields',model='test',max_tokens=8192)
        self.assertEqual(metadata['finish_reason'],'length')
        self.assertEqual(metadata['usage']['reasoning_tokens'],400)
        self.assertNotIn('private document content',''.join(logs.output))


class ExtractorRetryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.extractor=IntelligentExtractor('test-key','http://127.0.0.1:1')
    async def asyncTearDown(self):
        await self.extractor.client.close()
    def connection(self):
        return openai.APIConnectionError(request=httpx.Request('POST','http://127.0.0.1:1'))
    async def test_disconnect_retries_once_with_diagnostics(self):
        call=AsyncMock(side_effect=[self.connection(),'ok'])
        with (patch('intelligent_extractor.asyncio.sleep',new_callable=AsyncMock) as sleep,
              self.assertLogs('ocr.processing',level='WARNING') as logs):
            result=await self.extractor._call_with_retry('translate_fields',call,model='test-model',field_count=15)
        self.assertEqual(result,'ok');self.assertEqual(call.await_count,2)
        sleep.assert_awaited_once_with(2)
        message='\n'.join(logs.output)
        for value in ['translate_fields','test-model','字段数: 15','重试 1/1','已耗时']:
            self.assertIn(value,message)
    async def test_second_failure_propagates_without_third_call(self):
        call=AsyncMock(side_effect=[self.connection(),self.connection()])
        with patch('intelligent_extractor.asyncio.sleep',new_callable=AsyncMock) as sleep:
            with self.assertRaises(openai.APIConnectionError):
                await self.extractor._call_with_retry('test',call)
        self.assertEqual(call.await_count,2);sleep.assert_awaited_once_with(2)
    async def test_direct_protocol_error_is_supported(self):
        call=AsyncMock(side_effect=[httpx.RemoteProtocolError('disconnected'),'ok'])
        with patch('intelligent_extractor.asyncio.sleep',new_callable=AsyncMock):
            self.assertEqual(await self.extractor._call_with_retry('test',call),'ok')
        self.assertEqual(call.await_count,2)
    async def test_timeout_quota_and_other_errors_are_not_retried(self):
        request=httpx.Request('POST','http://127.0.0.1:1')
        errors=[openai.APITimeoutError(request=request),
                openai.PermissionDeniedError('quota',response=httpx.Response(403,request=request),body={'error':'quota'}),
                ValueError('JSON error')]
        for error in errors:
            call=AsyncMock(side_effect=error)
            with patch('intelligent_extractor.asyncio.sleep',new_callable=AsyncMock) as sleep:
                with self.assertRaises(type(error)):await self.extractor._call_with_retry('test',call)
            self.assertEqual(call.await_count,1);sleep.assert_not_awaited()
    async def test_all_three_calls_retry_and_parse_control_characters(self):
        cases=[('detect_document_type',lambda:self.extractor.detect_document_type('source','model'),'{"type":"other","reason":"a\nb"}'),
               ('extract_fields_intelligently',lambda:self.extractor.extract_fields_intelligently('source','other','其他','model'),'{"fields":{"name":{"value":"a\nb","confidence":0.9}}}'),
               ('translate_fields',lambda:self.extractor.translate_fields({'name':{'value':'source'}},'en','model'),'{"values":{"name":"a\nb"},"labels":{"name":"Name"}}')]
        for method,invoke,content in cases:
            call=AsyncMock(side_effect=[self.connection(),response(content)])
            self.extractor.client.chat.completions.create=call
            with patch('intelligent_extractor.asyncio.sleep',new_callable=AsyncMock):
                result=await invoke()
            self.assertTrue(result);self.assertEqual(call.await_count,2,method)
    async def test_exhausted_detection_and_extraction_connections_propagate(self):
        for invoke in [lambda:self.extractor.detect_document_type('source'),
                       lambda:self.extractor.extract_fields_intelligently('source','other','其他')]:
            call=AsyncMock(side_effect=[self.connection(),self.connection()])
            self.extractor.client.chat.completions.create=call
            with patch('intelligent_extractor.asyncio.sleep',new_callable=AsyncMock):
                with self.assertRaises(openai.APIConnectionError):await invoke()
            self.assertEqual(call.await_count,2)
    async def test_json_error_does_not_repeat_model_call(self):
        call=AsyncMock(return_value=response('{"values":'))
        self.extractor.client.chat.completions.create=call
        with tempfile.TemporaryDirectory() as tmp,patch('tempfile.tempdir',tmp),patch('intelligent_extractor.asyncio.sleep',new_callable=AsyncMock) as sleep:
            with self.assertRaisesRegex(RuntimeError,'JSON 解析失败'):
                await self.extractor.translate_fields({'name':{'value':'source'}})
            self.assertEqual(len(list(Path(tmp).glob('json_fail_*.txt'))),1)
        self.assertEqual(call.await_count,1);sleep.assert_not_awaited()
    async def test_output_limits_are_targeted(self):
        call=AsyncMock(return_value=response('{"fields":{},"values":{},"labels":{}}'))
        self.extractor.client.chat.completions.create=call
        await self.extractor.detect_document_type('source')
        self.assertEqual(call.call_args.kwargs['max_tokens'],1000)
        await self.extractor.extract_fields_intelligently('source','other','其他')
        self.assertEqual(call.call_args.kwargs['max_tokens'],8192)
        await self.extractor.translate_fields({'name':{'value':'source'}})
        self.assertEqual(call.call_args.kwargs['max_tokens'],8192)
    async def test_json_mode_is_set_on_all_extractor_calls(self):
        call=AsyncMock(return_value=response('{"fields":{},"values":{},"labels":{}}'))
        self.extractor.client.chat.completions.create=call
        for invoke in [lambda:self.extractor.detect_document_type('source'),
                       lambda:self.extractor.extract_fields_intelligently('source','other','其他'),
                       lambda:self.extractor.translate_fields({'name':{'value':'source'}})]:
            await invoke()
            kwargs=call.call_args.kwargs
            self.assertEqual(kwargs['response_format'],{'type':'json_object'})
            self.assertIn('JSON',' '.join(m['content'] for m in kwargs['messages']))
    async def test_prompt_examples_are_valid_json(self):
        call=AsyncMock(return_value=response('{"fields":{}}'))
        self.extractor.client.chat.completions.create=call
        await self.extractor.extract_fields_intelligently('source','other','其他')
        prompt=call.call_args.kwargs['messages'][1]['content']
        example=prompt.split('返回 JSON 格式：\n',1)[1].split('\n\n只返回',1)[0]
        self.assertIsInstance(json.loads(example)['fields']['字段英文名']['is_handwritten'],bool)


class GenericParserTest(unittest.IsolatedAsyncioTestCase):
    async def test_generic_connection_retry_is_bounded(self):
        for recover in (True, False):
            processor=MultiPageProcessor('test-key','http://127.0.0.1:1')
            def disconnected():
                return openai.APIConnectionError(request=httpx.Request('POST','http://127.0.0.1:1'))
            call=AsyncMock(side_effect=[disconnected(), response('{"fields":{"name":{"value":"sample"}}}') if recover else disconnected()])
            processor.client.chat.completions.create=call
            try:
                with (patch('intelligent_extractor.asyncio.sleep',new_callable=AsyncMock) as sleep,
                      self.assertLogs('ocr.processing',level='WARNING') as logs):
                    if recover:
                        result=await processor._extract_fields_generic('source','model')
                        self.assertEqual(result['fields']['name']['value'],'sample')
                    else:
                        with self.assertRaisesRegex(RuntimeError,'字段提取失败'):
                            await processor._extract_fields_generic('source','model')
                self.assertEqual(call.await_count,2)
                sleep.assert_awaited_once_with(2)
                self.assertIn('extract_fields_generic','\n'.join(logs.output))
            finally:
                await processor.client.close();await processor.extractor.client.close()

    async def test_generic_extraction_uses_same_parser(self):
        processor=MultiPageProcessor('test-key','http://127.0.0.1:1')
        processor.client.chat.completions.create=AsyncMock(return_value=response('{"fields":{"note":{"value":"a\nb"}}}'))
        try:
            result=await processor._extract_fields_generic('source','model')
            self.assertEqual(result['fields']['note']['value'],'a\nb')
            processor.client.chat.completions.create.assert_awaited_once()
            self.assertEqual(processor.client.chat.completions.create.call_args.kwargs["max_tokens"],8192)
            self.assertEqual(processor.client.chat.completions.create.call_args.kwargs["response_format"],{"type":"json_object"})
        finally:
            await processor.client.close();await processor.extractor.client.close()
