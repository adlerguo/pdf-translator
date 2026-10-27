"""
智能字段提取与证件类型识别模块
使用 LLM 动态提取字段，不依赖固定字段列表
"""
import json
import asyncio
import importlib
import logging
import time
from typing import Dict, List, Any
from pathlib import Path
import openai
from model_client import create_async_client
from document_language import normalize_language, LANGUAGE_NAMES
from json_parser import parse_json_safe, log_response_metadata


logger = logging.getLogger('ocr.processing')
_connection_errors = [openai.APIConnectionError]
for _module_name in ('httpx', 'httpx2'):
    try:
        _connection_errors.append(importlib.import_module(_module_name).RemoteProtocolError)
    except ImportError:
        pass
CONNECTION_ERRORS = tuple(_connection_errors)


class IntelligentExtractor:
    """智能字段提取器"""

    def __init__(self, api_key: str, base_url: str):
        self.client = create_async_client(api_key, base_url)

    async def _call_with_retry(self, method_name, api_call_func, *, model=None, field_count=None):
        """Retry connection failures once; SDK retries remain disabled."""
        started = time.monotonic()
        try:
            return await api_call_func()
        except CONNECTION_ERRORS as exc:
            # Timeouts are a subclass of APIConnectionError, but this change is
            # specifically for disconnected connections, not slow responses.
            if isinstance(exc, openai.APITimeoutError):
                raise
            logger.warning(
                '连接断开 | 方法: %s | 模型: %s | 已耗时: %.2fs | 字段数: %s | 异常: %s | 重试 1/1，等待 2 秒',
                method_name, model or 'unknown', time.monotonic() - started,
                field_count if field_count is not None else '不适用', type(exc).__name__)
            await asyncio.sleep(2)
            return await api_call_func()

    async def detect_document_type(self, ocr_text: str, model: str = "gemini-3.8-flash") -> Dict[str, Any]:
        """
        智能识别证件类型

        Args:
            ocr_text: OCR 识别的原始文本
            model: 使用的模型

        Returns:
            {
                "type": "driver_license",  # 证件类型
                "type_cn": "驾驶证",
                "confidence": 0.95,
                "features": ["包含驾驶证号码", ...],
                "recommended_template": "1405 徐俊 附件 英 [驾驶证]"
            }
        """
        prompt = f"""你是专业的证件识别专家。请分析以下 OCR 识别的文本，判断这是什么类型的证件。

OCR 文本：
{ocr_text}

可能的证件类型：
- driver_license: 驾驶证
- birth_certificate: 出生医学证明
- household_register: 户口簿（居民户口登记簿）
- marriage_certificate: 结婚证
- kinship_certificate: 亲属关系证明
- criminal_record: 无犯罪记录证明
- diploma: 毕业证书
- declaration: 声明书
- other: 其他类型

请返回 JSON 格式：
{{
    "type": "证件类型英文代码",
    "type_cn": "证件类型中文名称",
    "confidence": 0.95,
    "features": ["关键特征1", "关键特征2"],
    "reason": "判断理由"
}}

只返回合法 JSON，不要注释或其他内容；字符串中的换行和制表符必须按 JSON 规则转义。"""

        try:
            response = await self._call_with_retry(
                "detect_document_type",
                lambda: self.client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "你是专业的证件识别专家，擅长分析文档类型。"},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                    max_tokens=1000
                ), model=model, field_count=None
            )

            metadata = log_response_metadata(response, "detect_document_type", model=model, max_tokens=1000)
            result_text = response.choices[0].message.content
            return parse_json_safe(result_text, "detect_document_type", response_metadata=metadata)

        except CONNECTION_ERRORS:
            raise
        except Exception as e:
            print(f"证件类型识别失败: {e}")
            return {
                "type": "other",
                "type_cn": "未知证件",
                "confidence": 0.0,
                "features": [],
                "reason": f"识别失败: {str(e)}"
            }

    async def extract_fields_intelligently(
        self,
        ocr_text: str,
        doc_type: str,
        doc_type_cn: str,
        model: str = "gemini-3.8-flash"
    ) -> Dict[str, Any]:
        """
        智能提取所有字段（动态，不依赖固定字段列表）

        Args:
            ocr_text: OCR 识别的原始文本
            doc_type: 证件类型（英文）
            doc_type_cn: 证件类型（中文）
            model: 使用的模型

        Returns:
            {
                "fields": {
                    "name": {"value": "徐俊", "confidence": 0.98, "is_handwritten": false, "field_type": "姓名"},
                    "id_number": {"value": "...", "confidence": 0.95, ...},
                    ...
                },
                "tables": [
                    {"title": "准驾车型代号", "content": [...]}
                ],
                "seals": ["上海市公安局交通警察总队"],
                "dates": ["2021-07-30", "2031-07-30"]
            }
        """
        prompt = f"""你是专业的证件信息提取专家。请从以下 OCR 文本中提取所有可识别的字段信息。

证件类型：{doc_type_cn} ({doc_type})
OCR 文本：
{ocr_text}

要求：
1. 提取所有关键字段（姓名、证件号、地址、日期等）
2. 对每个字段评估置信度（0-1）
3. 判断哪些可能是手写内容（通常置信度较低、字体不规整）
4. 如果有表格内容，提取表格结构
5. 提取所有印章/公章信息
6. 提取所有日期信息

返回 JSON 格式：
{{
    "fields": {{
        "字段英文名": {{
            "value": "字段值",
            "field_type": "字段类型（中文）",
            "confidence": 0.95,
            "is_handwritten": false,
            "location": "字段在文档中的位置描述"
        }}
    }},
    "tables": [
        {{
            "title": "表格标题",
            "headers": ["列1", "列2"],
            "rows": [["值1", "值2"]]
        }}
    ],
    "seals": ["印章文字1", "印章文字2"],
    "dates": ["2021-07-30"],
    "handwritten_fields": ["地址", "备注"]
}}

只返回合法 JSON，不要注释或其他内容；字符串中的换行和制表符必须按 JSON 规则转义。"""

        try:
            response = await self._call_with_retry(
                "extract_fields_intelligently",
                lambda: self.client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "你是专业的证件信息提取专家，擅长从 OCR 文本中提取结构化信息。"},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                    max_tokens=8192
                ), model=model, field_count=None
            )

            metadata = log_response_metadata(response, "extract_fields", model=model, max_tokens=8192)
            result_text = response.choices[0].message.content
            extracted_data = parse_json_safe(result_text, "extract_fields", response_metadata=metadata)

            # 标记需要人工复核的字段
            extracted_data["review_required"] = self._mark_for_review(extracted_data)

            return extracted_data

        except CONNECTION_ERRORS:
            raise
        except Exception as e:
            print(f"字段提取失败: {e}")
            return {
                "fields": {},
                "tables": [],
                "seals": [],
                "dates": [],
                "handwritten_fields": [],
                "review_required": [],
                "error": str(e)
            }

    def _mark_for_review(self, extracted_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """标记需要人工复核的字段"""
        review_required = []

        for field_name, field_data in extracted_data.get("fields", {}).items():
            confidence = field_data.get("confidence", 0)
            is_handwritten = field_data.get("is_handwritten", False)

            # 置信度低于 0.8 或是手写体，需要复核
            if confidence < 0.8 or is_handwritten:
                review_required.append({
                    "field": field_name,
                    "field_type": field_data.get("field_type", field_name),
                    "value": field_data.get("value", ""),
                    "confidence": confidence,
                    "reason": "手写体识别" if is_handwritten else f"低置信度 ({confidence:.0%})",
                    "is_handwritten": is_handwritten
                })

        return review_required

    def _get_translation_guide(self, target_lang: str) -> Dict[str, str]:
        """根据目标语言返回翻译指南"""
        guides = {
            'en': {
                'lang_name': 'English',
                'rules': '1. 姓名使用拼音（姓在前，名在后，首字母大写）\n2. 地址详细翻译，保持原格式\n3. 日期格式：Month Day, Year（如 July 30, 2021）\n4. 保持专业术语的准确性'
            },
            'ja': {
                'lang_name': '日本語 (Japanese)',
                'rules': '1. 姓名は音読みまたは訓読みで表記\n2. 住所は日本語の順序で翻訳\n3. 日付形式：YYYY年MM月DD日\n4. 専門用語の正確性を保つ'
            },
            'ko': {
                'lang_name': '한국어 (Korean)',
                'rules': '1. 이름은 한국어 음역으로 표기\n2. 주소는 한국어 순서로 번역\n3. 날짜 형식：YYYY년 MM월 DD일\n4. 전문 용어의 정확성 유지'
            },
            'zh-CN': {
                'lang_name': '简体中文',
                'rules': '1. 姓名保持原文或音译\n2. 地址按中文习惯翻译\n3. 日期格式：YYYY年MM月DD日\n4. 保持专业术语准确性'
            },
            'zh-TW': {
                'lang_name': '繁體中文',
                'rules': '1. 姓名保持原文或音譯\n2. 地址按中文習慣翻譯\n3. 日期格式：YYYY年MM月DD日\n4. 保持專業術語準確性'
            },
            'fr': {
                'lang_name': 'Français (French)',
                'rules': '1. Noms en transcription phonétique\n2. Adresses traduites en respectant le format français\n3. Format de date : DD Month YYYY\n4. Maintenir la précision des termes techniques'
            },
            'de': {
                'lang_name': 'Deutsch (German)',
                'rules': '1. Namen in phonetischer Umschrift\n2. Adressen im deutschen Format übersetzen\n3. Datumsformat: DD. Month YYYY\n4. Fachbegriffe präzise übersetzen'
            },
            'es': {
                'lang_name': 'Español (Spanish)',
                'rules': '1. Nombres en transcripción fonética\n2. Direcciones traducidas al formato español\n3. Formato de fecha: DD de Month de YYYY\n4. Mantener precisión en términos técnicos'
            },
            'ru': {
                'lang_name': 'Русский (Russian)',
                'rules': '1. Имена в фонетической транскрипции\n2. Адреса переведены в русском формате\n3. Формат даты: DD Month YYYY г.\n4. Сохранять точность технических терминов'
            },
            'ar': {
                'lang_name': 'العربية (Arabic)',
                'rules': '1. الأسماء بالنقل الصوتي\n2. العناوين مترجمة بالتنسيق العربي\n3. تنسيق التاريخ: DD Month YYYY\n4. الحفاظ على دقة المصطلحات الفنية'
            },
        }

        target_lang = normalize_language(target_lang)
        return guides.get(target_lang, {'lang_name': LANGUAGE_NAMES[target_lang], 'rules': '姓名、地址、日期和专业术语遵循目标语言习惯；字段标签与字段值均使用目标语言，保留事实、数字与编号。'})

    async def translate_fields(
        self,
        fields: Dict[str, Any],
        target_lang: str = "en",
        model: str = "gemini-3.8-flash"
    ) -> Dict[str, str]:
        """
        翻译所有字段（包括字段名和字段值）

        Args:
            fields: 提取的字段字典
            target_lang: 目标语言
            model: 使用的模型

        Returns:
            翻译后的字典，包含：
            - 字段值翻译: {"字段名": "翻译后的值"}
            - 字段名翻译: {"字段名_label": "翻译后的字段名"}
        """
        # 准备待翻译内容（字段值 + 字段名）
        to_translate_values = {}
        field_labels = {}

        for field_name, field_data in fields.items():
            if isinstance(field_data, dict):
                # 字段值
                if "value" in field_data:
                    to_translate_values[field_name] = field_data["value"]
                # 字段名称（field_type）
                label = field_data.get("field_type", field_name)
                if str(label).lower() in {"string", "date", "number", "boolean", "text", "integer", "float"}:
                    label = field_name
                field_labels[field_name] = label

        if not to_translate_values:
            return {}

        # 根据目标语言生成翻译指南
        lang_guide = self._get_translation_guide(target_lang)

        prompt = f"""请翻译以下字段到{lang_guide['lang_name']}。

翻译规则：
{lang_guide['rules']}

字段值（需要翻译）：
{json.dumps(to_translate_values, ensure_ascii=False, indent=2)}

字段名称（必须翻译成{lang_guide['lang_name']}标签，与字段值使用同一目标语言）：
{json.dumps(field_labels, ensure_ascii=False, indent=2)}

返回 JSON 格式：
{{
    "values": {{
        "field_name": "translated value in {lang_guide['lang_name']}"
    }},
    "labels": {{
        "field_name": "字段名称的{lang_guide['lang_name']}翻译"
    }}
}}

要求：
1. values 中是字段值的翻译（翻译成目标语言）
2. labels 中是字段名称的{lang_guide['lang_name']}翻译，禁止统一改用英文（除非目标语言本身就是英文）
3. 只返回合法 JSON，不要注释或其他内容；字符串中的换行和制表符必须按 JSON 规则转义。"""

        try:
            response = await self._call_with_retry(
                "translate_fields",
                lambda: self.client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "你是专业的翻译助手，擅长证件文档的翻译。"},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.3,
                    max_tokens=8192
                ), model=model, field_count=len(to_translate_values)
            )

            metadata = log_response_metadata(response, "translate_fields", model=model, max_tokens=8192)
            result_text = response.choices[0].message.content
            result = parse_json_safe(result_text, "translate_fields", response_metadata=metadata)

            # 合并字段值和字段标签翻译
            translations = result.get("values", {})
            labels = result.get("labels", {})

            # 添加字段标签到返回结果中（用 _label 后缀区分）
            for field_name, label in labels.items():
                translations[f"{field_name}_label"] = label

            return translations

        except Exception as e:
            print(f"翻译失败: {e}")
            import traceback
            traceback.print_exc()
            raise RuntimeError(f"翻译失败：{e}") from e
