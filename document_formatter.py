"""
正式文档格式化模块
根据规范生成符合正式公证译文标准的 Word 文档
不是逐字段草稿，是整理成连续段落的正式成稿
支持多语言本地化
"""
from typing import Dict, Any, Optional, List
from pathlib import Path
from datetime import datetime
import sys
from docx import Document
from docx.shared import Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from document_language import normalize_language, chinese_text, chinese_field_label, document_title, document_date


# 多语言模板配置
LANGUAGE_TEMPLATES = {
    "en": {
        "translation_label": "Translation",
        "certificate_prefix": "No.",
        "certification_text": "This is to certify that the attached document is a true translation of the original.",
        "notary_label": "Notary",
        "page_label": "Page",
        "date_format": "%B %d, %Y",  # October 22, 2026
        "font": "Times New Roman"
    },
    "ko": {
        "translation_label": "번역문",
        "certificate_prefix": "번호",
        "certification_text": "이 문서는 원본의 정확한 번역임을 증명합니다.",
        "notary_label": "공증인",
        "page_label": "페이지",
        "date_format": "%Y년 %m월 %d일",  # 2026년 10월 22일
        "font": "Malgun Gothic"
    },
    "ja": {
        "translation_label": "翻訳文",
        "certificate_prefix": "番号",
        "certification_text": "これは原本の正確な翻訳であることを証明します。",
        "notary_label": "公証人",
        "page_label": "ページ",
        "date_format": "%Y年%m月%d日",  # 2026年10月22日
        "font": "MS Gothic"
    },
    "zh": {
        "translation_label": "译文",
        "certificate_prefix": "编号",
        "certification_text": "兹证明所附文件为原件之准确译文。",
        "notary_label": "公证员",
        "page_label": "页",
        "date_format": "%Y年%m月%d日",  # 2026年10月22日
        "font": "SimSun"
    },
    "es": {
        "translation_label": "Traducción",
        "certificate_prefix": "N.º",
        "certification_text": "Se certifica que el documento adjunto es una traducción fiel del original.",
        "notary_label": "Notario",
        "page_label": "Página",
        "date_format": "%d de %B de %Y",  # 22 de octubre de 2026
        "font": "Times New Roman"
    },
    "fr": {
        "translation_label": "Traduction",
        "certificate_prefix": "N°",
        "certification_text": "Nous certifions que le document ci-joint est une traduction fidèle de l'original.",
        "notary_label": "Notaire",
        "page_label": "Page",
        "date_format": "%d %B %Y",  # 22 octobre 2026
        "font": "Times New Roman"
    },
    "de": {
        "translation_label": "Übersetzung",
        "certificate_prefix": "Nr.",
        "certification_text": "Hiermit wird bestätigt, dass das beigefügte Dokument eine getreue Übersetzung des Originals ist.",
        "notary_label": "Notar",
        "page_label": "Seite",
        "date_format": "%d. %B %Y",  # 22. Oktober 2026
        "font": "Times New Roman"
    },
    "ru": {
        "translation_label": "Перевод",
        "certificate_prefix": "№",
        "certification_text": "Настоящим удостоверяется, что прилагаемый документ является точным переводом оригинала.",
        "notary_label": "Нотариус",
        "page_label": "Страница",
        "date_format": "%d %B %Y г.",  # 22 октября 2026 г.
        "font": "Times New Roman"
    }
}


# Keep every accepted language explicit: no Chinese or other UI selection may
# fall through to the English template.
LANGUAGE_TEMPLATES['zh-CN'] = LANGUAGE_TEMPLATES['zh']
LANGUAGE_TEMPLATES['zh-TW'] = {
    'translation_label':'譯文', 'certificate_prefix':'編號',
    'certification_text':'茲證明所附文件為原件之準確譯文。',
    'notary_label':'公證員', 'page_label':'頁', 'date_format':'%Y年%m月%d日',
    'font':'PMingLiU'}
for code, values in {
    'it': ('Traduzione','N.','Si certifica che il documento allegato è una traduzione fedele dell’originale.','Notaio','Pagina'),
    'pt': ('Tradução','N.º','Certifica-se que o documento anexo é uma tradução fiel do original.','Notário','Página'),
    'ar': ('الترجمة','رقم','نشهد بأن المستند المرفق ترجمة دقيقة للأصل.','كاتب العدل','صفحة'),
    'th': ('คำแปล','เลขที่','ขอรับรองว่าเอกสารแนบเป็นคำแปลที่ถูกต้องของต้นฉบับ','ผู้รับรองเอกสาร','หน้า'),
    'vi': ('Bản dịch','Số','Chứng nhận tài liệu đính kèm là bản dịch chính xác của bản gốc.','Công chứng viên','Trang'),
    'id': ('Terjemahan','No.','Dengan ini dinyatakan bahwa dokumen terlampir merupakan terjemahan yang akurat dari aslinya.','Notaris','Halaman'),
    'hi': ('अनुवाद','संख्या','प्रमाणित किया जाता है कि संलग्न दस्तावेज़ मूल का सटीक अनुवाद है।','नोटरी','पृष्ठ'),
}.items():
    LANGUAGE_TEMPLATES[code] = dict(zip(
        ('translation_label','certificate_prefix','certification_text','notary_label','page_label'), values),
        date_format='%Y-%m-%d', font='Times New Roman')


class DocumentFormatter:
    """正式文档格式化器（支持多语言）"""

    def __init__(self, target_lang: str = "en"):
        """
        初始化格式化器

        Args:
            target_lang: 目标语言代码 (en, ko, ja, zh, es, fr, de, ru)
        """
        self.target_lang = normalize_language(target_lang)
        self.template = dict(LANGUAGE_TEMPLATES[self.target_lang])
        if sys.platform == 'darwin' and self.target_lang.startswith('zh-'):
            self.template['font'] = 'Songti TC' if self.target_lang == 'zh-TW' else 'Songti SC'

    def create_formal_document(
        self,
        doc_type_cn: str,
        doc_type_en: str,
        fields: Dict[str, Any],
        translations: Dict[str, str],
        certificate_number: str = ""
    ) -> Document:
        """
        创建正式公证译文文档

        Args:
            doc_type_cn: 证件类型中文
            doc_type_en: 证件类型英文（已翻译为目标语言）
            fields: 提取的字段
            translations: 翻译后的字段
            certificate_number: 证书编号

        Returns:
            正式格式化的文档
        """
        doc = Document()

        # 1. 设置页面格式
        self._set_page_format(doc)

        # 2. 清除并设置默认样式
        self._clear_and_set_default_style(doc)

        # 3. 添加顶部"翻译文"标识（多语言）
        self._add_translation_label(doc)

        # 空行分隔
        doc.add_paragraph()

        # 4. 添加文档标题（居中、全大写、加粗）
        self._add_document_title(doc, document_title(doc_type_cn, self.target_lang) if self.target_lang.startswith("zh-") else (doc_type_en or document_title(None, self.target_lang)))

        # 5. 添加证书编号（如果有）
        if certificate_number:
            self._add_certificate_number(doc, certificate_number)

        # 空行分隔
        doc.add_paragraph()

        # 6. 添加正文内容（连续段落，不是逐字段）
        self._add_body_content(doc, fields, translations)

        # 空行分隔
        doc.add_paragraph()

        # 7. 添加落款部分（右侧缩进）
        self._add_signature_section(doc, translations)

        return doc

    def _set_page_format(self, doc: Document):
        """设置页面格式 - A4纵向，规范页边距"""
        section = doc.sections[0]

        # A4纸张
        section.page_width = Cm(21.0)
        section.page_height = Cm(29.7)

        # 页边距（按规范）
        section.top_margin = Cm(2.54)
        section.bottom_margin = Cm(2.54)
        section.left_margin = Cm(3.17)
        section.right_margin = Cm(3.17)
        section.header_distance = Cm(1.50)
        section.footer_distance = Cm(1.75)

    def _clear_and_set_default_style(self, doc: Document):
        """
        清除默认样式，设置统一格式
        根据目标语言选择合适的字体
        """
        style = doc.styles['Normal']

        # 字体
        font = style.font
        font.name = self.template["font"]
        font.size = Pt(14)
        font.color.rgb = RGBColor(0, 0, 0)
        font.bold = False

        # 中文/日文/韩文字体也使用目标语言字体
        style.element.rPr.rFonts.set(qn('w:eastAsia'), self.template["font"])
        from docx.oxml import OxmlElement
        lang = style.element.rPr.find(qn('w:lang'))
        if lang is None:
            lang = OxmlElement('w:lang')
            style.element.rPr.append(lang)
        lang.set(qn('w:val'), self.target_lang)
        lang.set(qn('w:eastAsia'), self.target_lang)

        # 段落格式
        pf = style.paragraph_format
        pf.space_before = Pt(0)
        pf.space_after = Pt(0)
        pf.line_spacing = Pt(20)  # 固定值20磅

    def _add_translation_label(self, doc: Document):
        """
        添加"翻译文"标识（多语言）
        左对齐，14磅，黑色，下划线
        """
        p = doc.add_paragraph()
        run = p.add_run(self.template["translation_label"])
        run.font.name = self.template["font"]
        run.font.size = Pt(14)
        run.font.color.rgb = RGBColor(0, 0, 0)
        run.font.bold = False
        run.underline = True

        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = Pt(20)

    def _add_document_title(self, doc: Document, title: str):
        """
        添加文档标题
        居中，全大写，14磅加粗，黑色
        """
        p = doc.add_paragraph()
        title = document_title(title, self.target_lang) if self.target_lang.startswith("zh-") else title
        run = p.add_run(title.upper())
        run.font.name = self.template["font"]
        run.font.size = Pt(14)
        run.font.color.rgb = RGBColor(0, 0, 0)
        run.font.bold = True

        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = Pt(20)

    def _add_certificate_number(self, doc: Document, number: str):
        """
        添加证书编号（多语言前缀）
        居中或适当偏右，加粗，14磅
        """
        p = doc.add_paragraph()
        run = p.add_run(f"{self.template['certificate_prefix']} {number}")
        run.font.name = self.template["font"]
        run.font.size = Pt(14)
        run.font.color.rgb = RGBColor(0, 0, 0)
        run.font.bold = True

        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = Pt(20)

    def _add_body_content(self, doc: Document, fields: Dict[str, Any], translations: Dict[str, str]):
        """
        添加正文内容
        动态显示所有字段，整理成连续段落
        使用翻译后的字段标签 + 翻译后的值
        """
        if not fields or not translations:
            # 如果没有字段，添加默认说明（多语言）
            p = doc.add_paragraph()
            run = p.add_run(self.template["certification_text"])
            run.font.name = self.template["font"]
            run.font.size = Pt(14)
            run.font.color.rgb = RGBColor(0, 0, 0)
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            return

        # 按字段类型分组
        field_groups = {
            'identity': [],  # 身份信息（姓名、性别、国籍等）
            'document': [],  # 证件信息（证件号、签发日期等）
            'address': [],   # 地址信息
            'other': []      # 其他信息
        }

        # 分类字段
        for field_name, field_data in fields.items():
            if not isinstance(field_data, dict) or 'value' not in field_data:
                continue

            field_type = field_data.get('field_type', field_name).lower()
            translated_value = translations.get(field_name, field_data['value'])
            if self.target_lang.startswith("zh-"):
                translated_value = chinese_text(translated_value, self.target_lang)

            if not translated_value or str(translated_value).strip() == '':
                continue

            # 获取翻译后的字段标签
            field_label = translations.get(f"{field_name}_label", field_data.get('field_type', field_name))
            if self.target_lang.startswith("zh-"):
                field_label = chinese_field_label(field_name, field_data, field_label, self.target_lang)

            field_info = {
                'name': field_name,
                'label': field_label,  # 使用翻译后的标签
                'value': translated_value
            }

            # 分组逻辑
            if any(k in field_type for k in ['name', '姓名', 'full_name']):
                field_groups['identity'].insert(0, field_info)  # 姓名放最前
            elif any(k in field_type for k in ['sex', 'gender', '性别', 'birth', '出生', 'nationality', '国籍']):
                field_groups['identity'].append(field_info)
            elif any(k in field_type for k in ['id', 'number', '证件', 'certificate', 'issue', 'valid', 'expiry', '签发', '有效']):
                field_groups['document'].append(field_info)
            elif any(k in field_type for k in ['address', '地址', 'residence']):
                field_groups['address'].append(field_info)
            else:
                field_groups['other'].append(field_info)

        # 第一段：证明文本（多语言）
        p = doc.add_paragraph()
        run = p.add_run(self.template["certification_text"])
        run.font.name = self.template["font"]
        run.font.size = Pt(14)
        run.font.color.rgb = RGBColor(0, 0, 0)
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT

        # 第二段：身份信息（用分号连接）
        if field_groups['identity']:
            p = doc.add_paragraph()
            parts = []
            for field in field_groups['identity']:
                parts.append(f"{field['label']}{'：' if self.target_lang.startswith('zh-') else ': '}{field['value']}")
            text = ("；".join(parts) + "。") if self.target_lang.startswith("zh-") else "; ".join(parts) + "."
            run = p.add_run(text)
            run.font.name = self.template["font"]
            run.font.size = Pt(14)
            run.font.color.rgb = RGBColor(0, 0, 0)
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT

        # 第三段：证件信息
        if field_groups['document']:
            p = doc.add_paragraph()
            parts = []
            for field in field_groups['document']:
                parts.append(f"{field['label']}{'：' if self.target_lang.startswith('zh-') else ': '}{field['value']}")
            text = ("；".join(parts) + "。") if self.target_lang.startswith("zh-") else "; ".join(parts) + "."
            run = p.add_run(text)
            run.font.name = self.template["font"]
            run.font.size = Pt(14)
            run.font.color.rgb = RGBColor(0, 0, 0)
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT

        # 第四段：地址信息
        if field_groups['address']:
            p = doc.add_paragraph()
            parts = []
            for field in field_groups['address']:
                parts.append(f"{field['label']}{'：' if self.target_lang.startswith('zh-') else ': '}{field['value']}")
            text = ("；".join(parts) + "。") if self.target_lang.startswith("zh-") else "; ".join(parts) + "."
            run = p.add_run(text)
            run.font.name = self.template["font"]
            run.font.size = Pt(14)
            run.font.color.rgb = RGBColor(0, 0, 0)
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT

        # 第五段：其他信息
        if field_groups['other']:
            p = doc.add_paragraph()
            parts = []
            for field in field_groups['other']:
                parts.append(f"{field['label']}{'：' if self.target_lang.startswith('zh-') else ': '}{field['value']}")
            text = ("；".join(parts) + "。") if self.target_lang.startswith("zh-") else "; ".join(parts) + "."
            run = p.add_run(text)
            run.font.name = self.template["font"]
            run.font.size = Pt(14)
            run.font.color.rgb = RGBColor(0, 0, 0)
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT

    def _get_field_value(self, fields: Dict[str, Any], translations: Dict[str, str], possible_keys: List[str]) -> str:
        """
        获取字段值（优先使用翻译）
        """
        for key in possible_keys:
            # 先找翻译
            if key in translations and translations[key]:
                return translations[key]
            # 再找原文
            if key in fields:
                field_data = fields[key]
                if isinstance(field_data, dict) and "value" in field_data:
                    return str(field_data["value"])
                elif isinstance(field_data, str):
                    return field_data
        return ""

    def _add_signature_section(self, doc: Document, translations: Dict[str, str]):
        """
        添加落款部分
        使用6.25cm左缩进形成右侧落款区
        """
        left_indent = Cm(6.25)

        # 公证处
        issuing_authority = self._get_field_value(
            {}, translations, ["issuing_authority", "issued_by", "签发机关"]
        )
        if issuing_authority:
            p = doc.add_paragraph()
            run = p.add_run(issuing_authority)
            run.font.name = self.template["font"]
            run.font.size = Pt(14)
            run.font.color.rgb = RGBColor(0, 0, 0)
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            p.paragraph_format.left_indent = left_indent

        # 公证员（如果有）- 使用多语言标签
        notary = translations.get("notary", "")
        if notary:
            p = doc.add_paragraph()
            run = p.add_run(f"{self.template['notary_label']}: {notary}")
            run.font.name = self.template["font"]
            run.font.size = Pt(14)
            run.font.color.rgb = RGBColor(0, 0, 0)
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            p.paragraph_format.left_indent = left_indent

        # 日期 - 使用多语言格式
        current_date = document_date(self.target_lang)
        p = doc.add_paragraph()
        run = p.add_run(current_date)
        run.font.name = self.template["font"]
        run.font.size = Pt(14)
        run.font.color.rgb = RGBColor(0, 0, 0)
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p.paragraph_format.left_indent = left_indent

    def merge_pages(
        self,
        pages_data: List[Dict[str, Any]],
        doc_type_cn: str,
        doc_type_en: str
    ) -> Document:
        """
        合并多页文档
        每页内容独立成页，不压缩
        """
        doc = Document()

        # 设置页面格式
        section = doc.sections[0]
        section.page_width = Cm(21.0)
        section.page_height = Cm(29.7)
        section.top_margin = Cm(2.54)
        section.bottom_margin = Cm(2.54)
        section.left_margin = Cm(3.17)
        section.right_margin = Cm(3.17)
        section.header_distance = Cm(1.50)
        section.footer_distance = Cm(1.75)

        self._clear_and_set_default_style(doc)

        # 遍历每一页
        for page_idx, page_data in enumerate(pages_data, 1):
            fields = page_data.get("extracted_fields", {}).get("fields", {})
            translations = page_data.get("translations", {})

            # 第1页：添加完整的标题结构
            if page_idx == 1:
                # 添加 Translation 标识（多语言）
                self._add_translation_label(doc)
                doc.add_paragraph()  # 空行

                # 添加文档标题
                title = document_title(doc_type_cn, self.target_lang) if self.target_lang.startswith("zh-") else (doc_type_en or document_title(None, self.target_lang))
                self._add_document_title(doc, title)
                doc.add_paragraph()  # 空行

            # 每页内容
            if page_idx > 1:
                # 第2页及以后：添加页码标识（多语言）
                p = doc.add_paragraph()
                run = p.add_run(f"（第 {page_idx} 頁）" if self.target_lang == "zh-TW" else f"（第 {page_idx} 页）" if self.target_lang == "zh-CN" else f"({self.template['page_label']} {page_idx})")
                run.font.name = self.template["font"]
                run.font.size = Pt(14)
                run.font.color.rgb = RGBColor(0, 0, 0)
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                doc.add_paragraph()  # 空行

            # 添加该页的正文内容
            self._add_body_content(doc, fields, translations)

            # 如果不是最后一页，插入分页符
            if page_idx < len(pages_data):
                doc.add_page_break()

        # 在最后一页添加落款
        doc.add_paragraph()  # 空行
        self._add_signature_section(doc, pages_data[-1].get("translations", {}))

        return doc

