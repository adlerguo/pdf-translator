"""
模板管理与匹配系统
自动匹配最合适的模板，或生成新模板
"""
import json
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
from docx import Document
import openai


class TemplateManager:
    """模板管理器"""

    def __init__(self, template_dir: Path, api_key: str, base_url: str):
        self.template_dir = template_dir
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self.template_cache = self._scan_templates()

    def _scan_templates(self) -> Dict[str, List[Dict[str, Any]]]:
        """扫描模板文件夹，提取模板信息"""
        templates = {
            "notarial": [],  # 公证书模板
            "attachment": []  # 附件/译文模板
        }

        if not self.template_dir.exists():
            return templates

        for file_path in self.template_dir.glob("*.docx"):
            file_name = file_path.name

            # 判断是公证书还是附件
            if "公证书" in file_name:
                template_type = "notarial"
            elif "附件" in file_name:
                template_type = "attachment"
            else:
                continue

            # 提取证件类型信息
            doc_type_hint = self._extract_doc_type_from_filename(file_name)

            templates[template_type].append({
                "path": file_path,
                "filename": file_name,
                "doc_type_hint": doc_type_hint
            })

        return templates

    def _extract_doc_type_from_filename(self, filename: str) -> str:
        """从文件名提取证件类型提示"""
        type_keywords = {
            "驾驶证": "driver_license",
            "出生": "birth_certificate",
            "户口": "household_register",
            "结婚": "marriage_certificate",
            "亲属": "kinship_certificate",
            "犯罪": "criminal_record",
            "毕业": "diploma",
            "声明": "declaration"
        }

        for keyword, doc_type in type_keywords.items():
            if keyword in filename:
                return doc_type

        return "other"

    async def find_best_template(
        self,
        doc_type: str,
        fields: Dict[str, Any],
        model: str = "gemini-3.8-flash"
    ) -> Dict[str, Any]:
        """
        查找最匹配的模板

        Returns:
            {
                "notarial_template": Path,
                "attachment_template": Path,
                "confidence": 0.95,
                "match_reason": "原因说明"
            }
        """
        # 准备候选模板信息
        candidates_info = {
            "notarial": [t["filename"] for t in self.template_cache["notarial"]],
            "attachment": [t["filename"] for t in self.template_cache["attachment"]]
        }

        prompt = f"""你是专业的模板匹配专家。请根据证件类型和提取的字段，找出最合适的模板。

证件类型：{doc_type}
提取的字段：{json.dumps(fields, ensure_ascii=False, indent=2)[:500]}

可用公证书模板：
{json.dumps(candidates_info['notarial'], ensure_ascii=False, indent=2)}

可用附件模板：
{json.dumps(candidates_info['attachment'], ensure_ascii=False, indent=2)}

要求：
1. 选择最匹配的公证书模板和附件模板
2. 给出匹配置信度（0-1）
3. 说明匹配理由

返回 JSON 格式：
{{
    "notarial_template": "文件名",
    "attachment_template": "文件名",
    "confidence": 0.0-1.0,
    "match_reason": "理由说明"
}}

只返回 JSON，不要其他内容。"""

        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是专业的模板匹配专家。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=1000
            )

            result_text = response.choices[0].message.content.strip()
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0].strip()

            match_result = json.loads(result_text)

            # 查找实际的文件路径
            notarial_path = self._find_template_path(match_result["notarial_template"], "notarial")
            attachment_path = self._find_template_path(match_result["attachment_template"], "attachment")

            if notarial_path and attachment_path:
                return {
                    "notarial_template": notarial_path,
                    "attachment_template": attachment_path,
                    "confidence": match_result["confidence"],
                    "match_reason": match_result["match_reason"],
                    "mode": "use_existing"
                }
            else:
                # 如果找不到模板，返回需要生成新模板的标记
                return {
                    "mode": "generate_new",
                    "confidence": 0.0,
                    "match_reason": "没有找到合适的模板，需要生成新模板"
                }

        except Exception as e:
            print(f"模板匹配失败: {e}")
            return {
                "mode": "generate_new",
                "confidence": 0.0,
                "match_reason": f"模板匹配失败: {str(e)}"
            }

    def _find_template_path(self, filename: str, template_type: str) -> Optional[Path]:
        """根据文件名查找模板路径"""
        for template in self.template_cache[template_type]:
            if template["filename"] == filename:
                return template["path"]
        return None

    def fill_template(
        self,
        template_path: Path,
        fields: Dict[str, Any],
        translations: Dict[str, str]
    ) -> Document:
        """
        填充模板
        动态替换模板中的内容

        Args:
            template_path: 模板文件路径
            fields: 提取的字段（原文）
            translations: 翻译的字段

        Returns:
            填充后的 Document 对象
        """
        doc = Document(template_path)

        # 替换段落中的内容
        for para in doc.paragraphs:
            self._replace_in_paragraph(para, fields, translations)

        # 替换表格中的内容
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for para in cell.paragraphs:
                        self._replace_in_paragraph(para, fields, translations)

        return doc

    def _replace_in_paragraph(
        self,
        paragraph,
        fields: Dict[str, Any],
        translations: Dict[str, str]
    ):
        """在段落中替换内容"""
        # 这是一个简化版本
        # 实际实现需要更复杂的逻辑来匹配和替换
        for field_name, field_data in fields.items():
            if isinstance(field_data, dict) and "value" in field_data:
                original_value = field_data["value"]
                translated_value = translations.get(field_name, original_value)

                # 替换原文
                if original_value and original_value in paragraph.text:
                    for run in paragraph.runs:
                        if original_value in run.text:
                            run.text = run.text.replace(original_value, str(original_value))

    def create_simple_document(
        self,
        doc_type_cn: str,
        fields: Dict[str, Any],
        translations: Dict[str, str] = None,
        include_translation: bool = False
    ) -> Document:
        """
        创建简单文档（当没有模板时使用）

        Args:
            doc_type_cn: 证件类型（中文）
            fields: 提取的字段
            translations: 翻译字段（可选）
            include_translation: 是否包含翻译

        Returns:
            新建的 Document 对象
        """
        doc = Document()

        # 添加标题
        title = f"{doc_type_cn}识别结果" if not include_translation else f"{doc_type_cn} Translation"
        heading = doc.add_heading(title, 0)
        heading.alignment = 1  # 居中

        # 添加字段内容
        for field_name, field_data in fields.items():
            if isinstance(field_data, dict) and "value" in field_data:
                field_type = field_data.get("field_type", field_name)
                value = field_data["value"]

                # 原文
                doc.add_heading(field_type, level=2)
                p = doc.add_paragraph(str(value))

                # 译文
                if include_translation and translations and field_name in translations:
                    translated_value = translations[field_name]
                    doc.add_paragraph(f"Translation: {translated_value}")
                    doc.add_paragraph()  # 空行

        return doc
