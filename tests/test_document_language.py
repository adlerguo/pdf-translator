"""Export language regressions: deterministic, no model calls or user data."""
import sys
from pathlib import Path
from datetime import datetime
import unittest
from unittest.mock import AsyncMock
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from document_formatter import DocumentFormatter
from document_language import normalize_language, document_title, document_date, LANGUAGE_NAMES
from intelligent_extractor import IntelligentExtractor


class DocumentLanguageTest(unittest.TestCase):
    def sample(self):
        return [{'page_num':1,'status':'success','extracted_fields':{'fields':{
            'applicant_name':{'field_type':'申请人姓名','value':'测试姓名'},
            '公证书编号':{'field_type':'string','value':'测试编号'}}},
            'translations':{'applicant_name':'測試姓名','applicant_name_label':'Applicant Name',
                            '公证书编号':'測試編號','公证书编号_label':'Notarial Certificate Number'}}]
    def test_traditional_export_repairs_legacy_labels_and_page_chrome(self):
        pages=self.sample()*2
        doc=DocumentFormatter('zh-TW').merge_pages(pages,'亲属关系证明','Kinship Certificate')
        text='\n'.join(p.text for p in doc.paragraphs)
        for expected in ['譯文','親屬關係證明','申請人姓名','公證書編號','茲證明','第 2 頁']:
            self.assertIn(expected,text)
        for wrong in ['Translation','KINSHIP','Applicant Name','Notarial Certificate Number','This is','Page 2','string','October']:
            self.assertNotIn(wrong,text)
        self.assertEqual(doc.styles['Normal'].element.rPr.rFonts.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia'),DocumentFormatter('zh-TW').template['font'])
    def test_simplified_alias_and_unknown_language(self):
        self.assertEqual(normalize_language('zh'),'zh-CN')
        self.assertEqual(normalize_language('zh_Hant'),'zh-TW')
        doc=DocumentFormatter('zh-CN').merge_pages(self.sample(),'亲属关系证明','Kinship Certificate')
        text='\n'.join(p.text for p in doc.paragraphs)
        self.assertIn('译文',text);self.assertIn('申请人姓名',text);self.assertNotIn('Translation',text)
        with self.assertRaises(ValueError):DocumentFormatter('unsupported')
    def test_every_accepted_language_has_explicit_chrome_and_locale_date(self):
        for lang in LANGUAGE_NAMES:
            with self.subTest(lang=lang):
                formatter=DocumentFormatter(lang)
                self.assertTrue(formatter.template['translation_label'])
                if lang!='en':self.assertNotEqual(formatter.template['translation_label'],'Translation')
                self.assertTrue(document_title(None,lang))
        self.assertEqual(document_date('zh-TW',datetime(2026,10,27)),'2026年10月27日')
        self.assertEqual(document_date('fr',datetime(2026,10,27)),'27 octobre 2026')
        self.assertEqual(document_date('en',datetime(2026,10,27)),'October 27, 2026')
    def test_formal_document_also_localizes_title(self):
        doc=DocumentFormatter('zh-TW').create_formal_document('亲属关系证明','Kinship Certificate',{}, {})
        self.assertIn('親屬關係證明','\n'.join(p.text for p in doc.paragraphs))


class TranslationPromptTest(unittest.IsolatedAsyncioTestCase):
    async def test_labels_follow_target_language(self):
        client=IntelligentExtractor('test-key','http://127.0.0.1:1')
        response=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"values":{"name":"測試"},"labels":{"name":"姓名"}}'))])
        call=AsyncMock(return_value=response);client.client.chat.completions.create=call
        try:
            result=await client.translate_fields({'name':{'value':'测试','field_type':'姓名'}},'zh-TW','model')
            prompt=call.call_args.kwargs['messages'][1]['content']
            self.assertIn('必须翻译成繁體中文标签',prompt)
            self.assertNotIn('统一用英文',prompt)
            self.assertNotIn('English Field Label',prompt)
            self.assertEqual(result['name_label'],'姓名')
            self.assertEqual(await client.translate_fields({},'zh-TW'),{})
        finally:await client.client.close()
