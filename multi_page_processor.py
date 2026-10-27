"""
多页PDF并发处理模块
支持多页同步OCR识别、翻译和合并输出
"""
import asyncio
from typing import List, Dict, Any, Optional
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import base64
from io import BytesIO
from PIL import Image
import openai
from model_client import create_async_client
import time
import logging
logger = logging.getLogger("ocr.processing")

from intelligent_extractor import IntelligentExtractor
from document_formatter import DocumentFormatter


class MultiPageProcessor:
    """多页处理器"""

    def __init__(self, api_key: str, base_url: str, max_concurrent: int = 5):
        """
        初始化多页处理器

        Args:
            api_key: API密钥
            base_url: API基础URL
            max_concurrent: 最大并发数(提高到5，加速处理)
        """
        self.client = create_async_client(api_key, base_url)
        self.extractor = IntelligentExtractor(api_key=api_key, base_url=base_url)
        # formatter会在export时根据target_lang动态创建
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def process_single_page(
        self,
        image_path: Path,
        page_num: int,
        ocr_model: str,
        extract_model: str,
        ocr_system_prompt: str,
        target_lang: str = "en"
    ) -> Dict[str, Any]:
        """
        处理单个页面

        Args:
            image_path: 图片路径
            page_num: 页码
            ocr_model: OCR模型
            extract_model: 提取模型
            ocr_system_prompt: OCR系统提示词
            target_lang: 目标语言

        Returns:
            处理结果
        """
        async with self.semaphore:  # 限制并发数
            try:
                page_start = time.time()
                print(f"[Page {page_num}] ⏱️ 开始处理...")

                stage = "ocr"
                stage_started = time.monotonic()
                # 1. OCR识别
                ocr_start = time.time()
                ocr_text = await self._ocr_image(image_path, ocr_model, ocr_system_prompt)
                ocr_time = time.time() - ocr_start
                print(f"[Page {page_num}] ✅ OCR完成 (耗时: {ocr_time:.2f}秒)")

                stage = "document_type"
                stage_started = time.monotonic()
                # 2. 证件类型识别(只在第一页识别)
                doc_type_result = None
                if page_num == 1:
                    detect_start = time.time()
                    doc_type_result = await self.extractor.detect_document_type(
                        ocr_text, extract_model
                    )
                    detect_time = time.time() - detect_start
                    print(f"[Page {page_num}] ✅ 证件类型识别: {doc_type_result.get('type_cn', '未知')} (耗时: {detect_time:.2f}秒)")

                stage = "extraction"
                stage_started = time.monotonic()
                # 3. 字段提取（所有页面都要提取）
                extract_start = time.time()
                if doc_type_result:
                    # 首页使用智能提取
                    extracted_fields = await self.extractor.extract_fields_intelligently(
                        ocr_text,
                        doc_type_result["type"],
                        doc_type_result["type_cn"],
                        extract_model
                    )
                else:
                    # 非首页使用通用提取
                    extracted_fields = await self._extract_fields_generic(
                        ocr_text, extract_model
                    )
                extract_time = time.time() - extract_start
                print(f"[Page {page_num}] ✅ 字段提取完成 (耗时: {extract_time:.2f}秒)")

                if extracted_fields.get("error"):
                    raise RuntimeError(extracted_fields["error"])
                if not extracted_fields.get("fields"):
                    raise RuntimeError("未提取到可用字段，请检查原图或调整模型")

                stage = "translation"
                stage_started = time.monotonic()
                # 4. 翻译字段（所有页面都要翻译）
                translations = {}
                if extracted_fields.get("fields"):
                    translate_start = time.time()
                    translations = await self.extractor.translate_fields(
                        extracted_fields.get("fields", {}),
                        target_lang=target_lang,
                        model=extract_model
                    )
                    translate_time = time.time() - translate_start
                    print(f"[Page {page_num}] ✅ 翻译完成，翻译了 {len(translations)} 个字段 (耗时: {translate_time:.2f}秒)")
                else:
                    print(f"[Page {page_num}] ⚠️ 没有字段需要翻译")

                page_total = time.time() - page_start
                print(f"[Page {page_num}] 🎉 页面处理完成 (总耗时: {page_total:.2f}秒)")

                return {
                    "page_num": page_num,
                    "ocr_text": ocr_text,
                    "doc_type": doc_type_result.get("type") if doc_type_result else None,
                    "doc_type_cn": doc_type_result.get("type_cn") if doc_type_result else None,
                    "extracted_fields": extracted_fields,
                    "translations": translations,
                    "review_required": extracted_fields.get("review_required", []),
                    "status": "success",
                    "timing": {
                        "ocr": f"{ocr_time:.2f}s",
                        "extract": f"{extract_time:.2f}s",
                        "translate": f"{translate_time if translations else 0:.2f}s",
                        "total": f"{page_total:.2f}s"
                    }
                }

            except Exception as e:
                logger.error("page=%s stage=%s elapsed=%.2fs error=%s", page_num, locals().get("stage", "unknown"), time.monotonic()-locals().get("stage_started", time.monotonic()), type(e).__name__)
                print(f"[Page {page_num}] 处理失败: {str(e)}")
                import traceback
                traceback.print_exc()
                return {
                    "page_num": page_num,
                    "status": "error",
                    "ocr_text": locals().get("ocr_text", ""),
                    "extracted_fields": locals().get("extracted_fields", {}),
                    "translations": locals().get("translations", {}),
                    "failed_stage": locals().get("stage", "unknown"),
                    "error": str(e),
                    "timing": {"failed_stage_seconds": round(time.monotonic()-locals().get("stage_started", time.monotonic()), 2)}
                }

    async def process_multiple_pages(
        self, image_paths, ocr_model, extract_model, ocr_system_prompt,
        target_lang="en", on_page=None
    ):
        async def run(index, path):
            result = await self.process_single_page(
                path, index, ocr_model, extract_model, ocr_system_prompt, target_lang)
            if on_page:
                await on_page(result)
            return result
        results = await asyncio.gather(*(run(i, path) for i, path in enumerate(image_paths, 1)))
        return sorted(results, key=lambda r: r["page_num"])

    async def _ocr_image(
        self,
        image_path: Path,
        model: str,
        system_prompt: str
    ) -> str:
        """OCR识别图片"""
        # 压缩图片
        img = Image.open(image_path)
        max_size = 2048
        if max(img.size) > max_size:
            ratio = max_size / max(img.size)
            new_size = tuple(int(dim * ratio) for dim in img.size)
            img = img.resize(new_size, Image.Resampling.LANCZOS)

        # 转换为RGB
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
            img = background

        # 保存为JPEG
        buffer = BytesIO()
        img.save(buffer, format='JPEG', quality=85, optimize=True)
        img_base64 = base64.b64encode(buffer.getvalue()).decode()

        response = await self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请识别图片中的所有文字,按原文排版输出:"},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{img_base64}"}
                        }
                    ]
                }
            ],
            max_tokens=4096,
            temperature=0.1
        )

        return response.choices[0].message.content.strip()

    async def _extract_fields_generic(
        self,
        ocr_text: str,
        model: str
    ) -> Dict[str, Any]:
        """通用字段提取(非首页)"""
        prompt = f"""请从以下OCR文本中提取所有可识别的信息。

OCR文本:
{ocr_text}

返回JSON格式:
{{
    "fields": {{
        "字段名": {{
            "value": "字段值",
            "field_type": "字段类型",
            "confidence": 0.0-1.0
        }}
    }},
    "tables": [],
    "seals": [],
    "dates": []
}}

只返回JSON,不要其他内容。"""

        try:
            response = await self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是专业的信息提取专家。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=4096
            )

            result_text = response.choices[0].message.content.strip()
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0].strip()

            import json
            return json.loads(result_text)

        except Exception as e:
            print(f"字段提取失败: {e}")
            raise RuntimeError(f"字段提取失败：{e}") from e

    def export_merged_document(
        self,
        pages_data: List[Dict[str, Any]],
        output_path: Path,
        target_lang: str = "en"
    ) -> Path:
        """
        导出合并后的文档

        Args:
            pages_data: 所有页面的数据
            output_path: 输出路径
            target_lang: 目标语言

        Returns:
            输出文件路径
        """
        # 从第一页获取文档类型
        first_page = pages_data[0]
        doc_type_cn = first_page.get("doc_type_cn", "证件")

        # 使用目标语言翻译文档类型
        doc_type_translated = self._translate_doc_type(doc_type_cn, target_lang)

        # 创建对应语言的格式化器
        formatter = DocumentFormatter(target_lang=target_lang)

        # 使用格式化器合并文档
        doc = formatter.merge_pages(pages_data, doc_type_cn, doc_type_translated)

        # 保存文档
        doc.save(output_path)
        print(f"合并文档已保存: {output_path} (语言: {target_lang})")

        return output_path

    def _translate_doc_type(self, doc_type_cn: str, target_lang: str = "en") -> str:
        """
        翻译文档类型名称到目标语言

        Args:
            doc_type_cn: 中文文档类型
            target_lang: 目标语言代码

        Returns:
            翻译后的文档类型
        """
        # 多语言文档类型映射
        type_map = {
            "驾驶证": {
                "en": "Driver's License",
                "ko": "운전면허증",
                "ja": "運転免許証",
                "zh": "驾驶证",
                "es": "Licencia de Conducir",
                "fr": "Permis de Conduire",
                "de": "Führerschein",
                "ru": "Водительское удостоверение"
            },
            "出生医学证明": {
                "en": "Birth Certificate",
                "ko": "출생증명서",
                "ja": "出生証明書",
                "zh": "出生医学证明",
                "es": "Certificado de Nacimiento",
                "fr": "Acte de Naissance",
                "de": "Geburtsurkunde",
                "ru": "Свидетельство о рождении"
            },
            "户口簿": {
                "en": "Household Register",
                "ko": "호적등본",
                "ja": "戸籍謄本",
                "zh": "户口簿",
                "es": "Registro del Hogar",
                "fr": "Registre de Ménage",
                "de": "Haushaltsregister",
                "ru": "Домовая книга"
            },
            "结婚证": {
                "en": "Marriage Certificate",
                "ko": "혼인증명서",
                "ja": "結婚証明書",
                "zh": "结婚证",
                "es": "Certificado de Matrimonio",
                "fr": "Certificat de Mariage",
                "de": "Heiratsurkunde",
                "ru": "Свидетельство о браке"
            },
            "亲属关系证明": {
                "en": "Kinship Certificate",
                "ko": "친족관계증명서",
                "ja": "親族関係証明書",
                "zh": "亲属关系证明",
                "es": "Certificado de Parentesco",
                "fr": "Certificat de Parenté",
                "de": "Verwandtschaftsbescheinigung",
                "ru": "Справка о родстве"
            },
            "无犯罪记录证明": {
                "en": "Certificate of No Criminal Record",
                "ko": "범죄경력증명서",
                "ja": "犯罪経歴証明書",
                "zh": "无犯罪记录证明",
                "es": "Certificado de Antecedentes Penales",
                "fr": "Certificat de Casier Judiciaire",
                "de": "Führungszeugnis",
                "ru": "Справка об отсутствии судимости"
            },
            "毕业证书": {
                "en": "Diploma",
                "ko": "졸업증명서",
                "ja": "卒業証明書",
                "zh": "毕业证书",
                "es": "Diploma",
                "fr": "Diplôme",
                "de": "Diplom",
                "ru": "Диплом"
            },
            "声明书": {
                "en": "Declaration",
                "ko": "선언서",
                "ja": "宣言書",
                "zh": "声明书",
                "es": "Declaración",
                "fr": "Déclaration",
                "de": "Erklärung",
                "ru": "Заявление"
            }
        }

        # 获取对应的翻译
        if doc_type_cn in type_map:
            return type_map[doc_type_cn].get(target_lang, type_map[doc_type_cn]["en"])

        # 默认返回 "Document" 的多语言版本
        default_doc = {
            "en": "Document",
            "ko": "문서",
            "ja": "書類",
            "zh": "文档",
            "es": "Documento",
            "fr": "Document",
            "de": "Dokument",
            "ru": "Документ"
        }
        return default_doc.get(target_lang, "Document")
