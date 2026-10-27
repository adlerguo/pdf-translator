"""
更新后的 FastAPI 后端
集成智能字段提取、模板匹配、双文档导出功能
新增任务队列管理、WebSocket实时通知、任务持久化存储
"""
import json
import os
import base64
from pathlib import Path
from typing import List, Optional, Dict, Any
import uuid
import asyncio
import re
import shutil
import tempfile
import logging

from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Body, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import openai
from pdf2image import convert_from_path
from PIL import Image
from docx import Document
from docx.shared import Pt, RGBColor
from pydantic import BaseModel

# 导入新模块
from intelligent_extractor import IntelligentExtractor
from template_manager import TemplateManager
from multi_page_processor import MultiPageProcessor
from document_formatter import DocumentFormatter
from task_storage import TaskStorage, TaskStatus
from task_queue import TaskQueue

# 初始化
app = FastAPI(title="PDF Translator Pro")
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = Path(os.getenv("OCR_UPLOAD_DIR", str(BASE_DIR / "uploads")))
OUTPUT_DIR = Path(os.getenv("OCR_OUTPUT_DIR", str(BASE_DIR / "outputs")))
processing_logger = logging.getLogger('ocr.processing')
processing_logger.setLevel(logging.INFO)
if not processing_logger.handlers:
    handler = logging.FileHandler(BASE_DIR / 'processing.log', encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    processing_logger.addHandler(handler)

# Portable default; retain the sibling template folder used by the original checkout.
DEFAULT_TEMPLATE_DIR = BASE_DIR / "templates"
if (BASE_DIR.parent / "模板").is_dir():
    DEFAULT_TEMPLATE_DIR = BASE_DIR.parent / "模板"
TEMPLATE_DIR = Path(os.getenv("OCR_TEMPLATE_DIR", str(DEFAULT_TEMPLATE_DIR))).expanduser()

for d in [UPLOAD_DIR, OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

# 加载配置
with open(BASE_DIR / "config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

# 初始化 OpenAI 客户端
client = openai.OpenAI(
    api_key=config["api"]["api_key"],
    base_url=config["api"]["base_url"]
)

# 初始化智能提取器和模板管理器
extractor = IntelligentExtractor(
    api_key=config["api"]["api_key"],
    base_url=config["api"]["base_url"]
)

template_manager = TemplateManager(
    template_dir=TEMPLATE_DIR,
    api_key=config["api"]["api_key"],
    base_url=config["api"]["base_url"]
)

# 初始化多页处理器
multi_page_processor = MultiPageProcessor(
    api_key=config["api"]["api_key"],
    base_url=config["api"]["base_url"],
    max_concurrent=3  # 最多3个页面同时处理
)

# 初始化任务队列系统
DB_PATH = Path(os.getenv("OCR_DATA_DIR", str(BASE_DIR / "data"))) / "tasks.db"
DB_PATH.parent.mkdir(exist_ok=True)

task_storage = TaskStorage(db_path=DB_PATH)
task_queue = TaskQueue(
    storage=task_storage,
    processor=multi_page_processor,
    max_concurrent_tasks=config.get("queue", {}).get("max_concurrent_tasks", 2),
    max_retries=config.get("queue", {}).get("max_retries", 3)
)

# WebSocket 连接管理器
class ConnectionManager:
    """WebSocket 连接管理器"""
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        """广播消息到所有连接"""
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                disconnected.append(connection)

        # 清理断开的连接
        for connection in disconnected:
            self.active_connections.remove(connection)

ws_manager = ConnectionManager()

# 设置任务队列回调
async def on_task_start(task_id: str, task_info: dict):
    """任务开始回调"""
    await ws_manager.broadcast({
        "type": "task_start",
        "task_id": task_id,
        "data": task_info
    })

async def on_task_complete(task_id: str, result_data: dict):
    """任务完成回调"""
    await ws_manager.broadcast({
        "type": "task_complete",
        "task_id": task_id,
        "data": result_data
    })

async def on_task_error(task_id: str, error_msg: str):
    """任务错误回调"""
    await ws_manager.broadcast({
        "type": "task_error",
        "task_id": task_id,
        "error": error_msg
    })

task_queue.on_task_start = on_task_start
task_queue.on_task_complete = on_task_complete
task_queue.on_task_error = on_task_error

# 数据模型
class PageData(BaseModel):
    page_num: int
    ocr_text: str
    translated_text: str = ""
    image_path: str
    extracted_fields: Optional[Dict[str, Any]] = None
    doc_type: Optional[str] = None


class ExportRequest(BaseModel):
    job_id: str
    pages: List[Dict[str, Any]]
    doc_type: str
    doc_type_cn: str
    extracted_fields: Dict[str, Any]
    translations: Dict[str, str]


# ============ 核心功能 ============

async def pdf_to_images(pdf_path: Path) -> List[Path]:
    """PDF 转图片"""
    try:
        images = await asyncio.to_thread(convert_from_path, str(pdf_path), dpi=200, timeout=120)
        image_paths = []

        job_id = pdf_path.stem
        job_dir = UPLOAD_DIR / job_id
        job_dir.mkdir(exist_ok=True)

        for i, img in enumerate(images):
            img_path = job_dir / f"page_{i+1}.png"
            img.save(img_path, "PNG")
            image_paths.append(img_path)

        return image_paths
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF 转换失败: {str(e)}")


async def ocr_image(image_path: Path, model: str) -> str:
    """OCR 识别图片"""
    try:
        # 压缩图片
        img = Image.open(image_path)
        max_size = 2048
        if max(img.size) > max_size:
            ratio = max_size / max(img.size)
            new_size = tuple(int(dim * ratio) for dim in img.size)
            img = img.resize(new_size, Image.Resampling.LANCZOS)

        # 转换为 RGB
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
            img = background

        # 保存为 JPEG
        from io import BytesIO
        buffer = BytesIO()
        img.save(buffer, format='JPEG', quality=85, optimize=True)
        img_base64 = base64.b64encode(buffer.getvalue()).decode()

        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": config["ocr"]["system_prompt"]
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "请识别图片中的所有文字，按原文排版输出："
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{img_base64}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=4096,
            temperature=0.1
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        print(f"OCR 错误详情: {error_detail}")
        raise HTTPException(status_code=500, detail=f"OCR 识别失败: {str(e)}")


# ============ 启动和关闭事件 ============

@app.on_event("startup")
async def startup_event():
    """应用启动时初始化任务队列"""
    await task_queue.start()
    processing_logger.info("service_started model_read_timeout=%ss sdk_retries=0 extractor_connection_retries=1", os.getenv("OCR_MODEL_READ_TIMEOUT", "300"))
    print("✅ 任务队列系统已启动")


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时停止任务队列"""
    await task_queue.stop()
    print("✅ 任务队列系统已停止")


# ============ WebSocket 端点 ============

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 连接，用于实时推送任务状态"""
    await ws_manager.connect(websocket)
    try:
        while True:
            # 保持连接，接收心跳
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# ============ API 端点 ============

@app.get("/")
async def root():
    """返回 HTML 页面"""
    return FileResponse(BASE_DIR / "static" / "workbench.html", headers={"Cache-Control": "no-store"})


MAX_UPLOAD_BYTES = 50 * 1024 * 1024
SUPPORTED_EXTENSIONS = {'.pdf', '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff', '.tif', '.gif'}


def valid_job_dir(job_id):
    if not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", job_id):
        raise HTTPException(400, "无效的文件编号")
    return UPLOAD_DIR / job_id


def page_paths(job_dir):
    return sorted(job_dir.glob("page_*.png"), key=lambda p: int(p.stem.split('_')[-1]))


def normalize_image(source, dest):
    with Image.open(source) as img:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
        if img.width * img.height > 60_000_000:
            raise ValueError("图片像素过大，请压缩至 6000 万像素以内")
        if img.mode in ('RGBA', 'LA', 'P'):
            rgba = img.convert('RGBA')
            background = Image.new('RGB', rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.getchannel('A'))
            img = background
        img.convert('RGB').save(dest, 'PNG')


@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)):
    filename = Path(file.filename or '未命名').name
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(400, "不支持的格式，请选择 PDF、PNG、JPG、WebP、BMP、TIFF 或 GIF")
    job_id = str(uuid.uuid4())
    job_dir = UPLOAD_DIR / job_id
    job_dir.mkdir()
    source = job_dir / ('source' + suffix)
    try:
        size = 0
        with source.open('wb') as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, f"{filename} 超过 50MB，请压缩后上传")
                out.write(chunk)
        if not size:
            raise HTTPException(400, "文件为空")
        if suffix == '.pdf':
            # Render directly to disk in a bounded worker thread, not the API loop.
            def render():
                from pdf2image import pdfinfo_from_path
                count = int(pdfinfo_from_path(str(source), timeout=30)['Pages'])
                if count > 100:
                    raise ValueError("单份 PDF 最多 100 页，请拆分后上传")
                for number in range(1, count + 1):
                    images = convert_from_path(str(source), dpi=200, first_page=number,
                                               last_page=number, timeout=120)
                    images[0].save(job_dir / f'page_{number}.png', 'PNG')
                    images[0].close()
            await asyncio.to_thread(render)
        else:
            await asyncio.to_thread(normalize_image, source, job_dir / 'page_1.png')
        result = {'job_id': job_id, 'filename': filename, 'size': size,
                  'total_pages': len(page_paths(job_dir)),
                  'preview_url': f'/api/image/{job_id}/1'}
        (job_dir / 'asset.json').write_text(json.dumps(result, ensure_ascii=False))
        return result
    except HTTPException:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(400, f"无法读取文件：{exc}")
    finally:
        await file.close()


@app.get("/api/assets/{job_id}")
async def get_asset(job_id: str):
    job_dir = valid_job_dir(job_id)
    manifest = job_dir / 'asset.json'
    if not manifest.exists():
        raise HTTPException(404, "素材不存在，请重新上传")
    return json.loads(manifest.read_text())


@app.post("/api/intelligent-process")
async def intelligent_process(
    job_id: str = Form(...),
    page_num: int = Form(...),
    ocr_model: str = Form("gemini-3.8-flash"),
    extract_model: str = Form("gemini-3.8-flash")
):
    """
    智能处理流程：OCR → 证件识别 → 字段提取
    """
    job_dir = UPLOAD_DIR / job_id
    image_path = job_dir / f"page_{page_num}.png"

    if not image_path.exists():
        raise HTTPException(status_code=404, detail="页面图片不存在")

    try:
        # 1. OCR 识别
        ocr_text = await ocr_image(image_path, ocr_model)

        # 2. 证件类型识别
        doc_type_result = await extractor.detect_document_type(ocr_text, extract_model)

        # 3. 智能字段提取
        extracted_fields = await extractor.extract_fields_intelligently(
            ocr_text,
            doc_type_result["type"],
            doc_type_result["type_cn"],
            extract_model
        )

        return {
            "page_num": page_num,
            "ocr_text": ocr_text,
            "doc_type": doc_type_result["type"],
            "doc_type_cn": doc_type_result["type_cn"],
            "confidence": doc_type_result["confidence"],
            "extracted_fields": extracted_fields,
            "review_required": extracted_fields.get("review_required", []),
            "image_url": f"/api/image/{job_id}/{page_num}"
        }

    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        print(f"智能处理错误: {error_detail}")
        raise HTTPException(status_code=500, detail=f"处理失败: {str(e)}")


@app.post("/api/translate-fields")
async def translate_fields_api(
    fields: Dict[str, Any] = Body(...),
    target_lang: str = Body("en"),
    model: str = Body("gemini-3.8-flash")
):
    """翻译所有字段"""
    try:
        translations = await extractor.translate_fields(fields, target_lang, model)
        return {"translations": translations}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"翻译失败: {str(e)}")


@app.post("/api/match-template")
async def match_template_api(
    doc_type: str = Body(...),
    fields: Dict[str, Any] = Body(...),
    model: str = Body("gemini-3.8-flash")
):
    """匹配最合适的模板"""
    try:
        match_result = await template_manager.find_best_template(doc_type, fields, model)
        return {
            "mode": match_result.get("mode", "use_existing"),
            "confidence": match_result.get("confidence", 0),
            "match_reason": match_result.get("match_reason", ""),
            "has_template": match_result.get("mode") == "use_existing"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"模板匹配失败: {str(e)}")


@app.post("/api/process-all-pages")
async def process_all_pages(
    job_id: str = Form(...),
    ocr_model: str = Form("gemini-3.8-flash"),
    extract_model: str = Form("gemini-3.8-flash"),
    target_lang: str = Form("en")
):
    """
    并发处理所有页面 (新增)
    OCR识别 + 证件识别 + 字段提取 + 翻译
    """
    job_dir = UPLOAD_DIR / job_id

    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="任务不存在")

    # 获取所有页面图片
    image_paths = sorted(job_dir.glob("page_*.png"))

    if not image_paths:
        raise HTTPException(status_code=404, detail="未找到页面图片")

    try:
        print(f"开始处理，目标语言: {target_lang}")
        # 并发处理所有页面
        results = await multi_page_processor.process_multiple_pages(
            image_paths,
            ocr_model,
            extract_model,
            config["ocr"]["system_prompt"],
            target_lang  # ✅ 传递目标语言参数
        )

        return {
            "job_id": job_id,
            "total_pages": len(results),
            "pages": results,
            "message": "所有页面处理完成"
        }

    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        print(f"多页处理错误: {error_detail}")
        raise HTTPException(status_code=500, detail=f"处理失败: {str(e)}")


# ============ 任务队列 API 端点 ============

@app.post("/api/tasks/add")
async def add_task_to_queue(
    job_id: str = Form(...),
    filename: str = Form(...),
    ocr_model: str = Form("gemini-3.8-flash"),
    extract_model: str = Form("gemini-3.8-flash"),
    target_lang: str = Form("en"),
    total_pages: int = Form(0),
    auto_start: bool = Form(False),
    request_id: Optional[str] = Form(None)
):
    """
    添加任务到队列（默认为uploaded状态，需要手动开始）

    Args:
        job_id: 作业ID
        filename: 文件名
        ocr_model: OCR模型
        extract_model: 提取模型
        target_lang: 目标语言
        total_pages: 总页数
        auto_start: 是否自动开始处理
    """
    if request_id and not re.fullmatch(r"[a-zA-Z0-9-]{8,80}", request_id):
        raise HTTPException(400, "提交编号无效")
    if request_id:
        existing = task_storage.get_task(request_id)
        if existing:
            if any(existing[k] != v for k, v in {
                'job_id': job_id, 'filename': filename, 'ocr_model': ocr_model,
                'extract_model': extract_model, 'target_lang': target_lang}.items()):
                raise HTTPException(409, "此提交编号已用于其他参数，请核对任务")
            return {k: existing[k] for k in ('task_id', 'job_id', 'total_pages', 'status')}
    job_dir = valid_job_dir(job_id)
    image_paths = page_paths(job_dir)
    if not image_paths:
        raise HTTPException(404, "未找到上传文件，请重新上传")
    available = {m['id'] for m in config['models']['available']}
    ocr_available = {m['id'] for m in config['models']['available'] if 'ocr' in m.get('use_for', ['ocr'])}
    if ocr_model not in ocr_available or extract_model not in available:
        raise HTTPException(400, "所选模型未在本机配置中启用")
    if target_lang not in {'en', 'zh', 'zh-CN', 'zh-TW', 'ja', 'ko', 'fr', 'de', 'es', 'it', 'pt', 'ru', 'th', 'vi', 'id', 'hi', 'ar'}:
        raise HTTPException(400, "不支持的目标语言")

    try:
        task_id = await task_queue.add_task(
            job_id=job_id,
            filename=filename,
            image_paths=image_paths,
            ocr_model=ocr_model,
            extract_model=extract_model,
            target_lang=target_lang,
            ocr_system_prompt=config["ocr"]["system_prompt"],
            auto_start=auto_start,
            task_id=request_id
        )

        return {
            "task_id": task_id,
            "job_id": job_id,
            "total_pages": len(image_paths),
            "status": "pending" if auto_start else "uploaded",
            "message": "任务已添加到队列" if auto_start else "任务已创建，点击开始处理"
        }

    except Exception as e:
        import traceback
        print(f"添加任务失败: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"添加任务失败: {str(e)}")


@app.get("/api/tasks/list")
async def list_tasks(limit: int = 50):
    """获取所有任务列表"""
    try:
        tasks = await task_queue.get_all_tasks(limit=limit)
        return {"tasks": [{k: v for k, v in t.items() if k not in ("config", "result_data")} | {"success_pages": (t.get("result_data") or {}).get("success_pages", 0), "failed_pages": (t.get("result_data") or {}).get("failed_pages", 0), "retry_translation_available": t['status'] in ('failed', 'partial', 'unknown') and any(p.get('status') == 'error' and (p.get('failed_stage') == 'translation' or str(p.get('error', '')).startswith('翻译失败')) and bool((p.get('extracted_fields') or {}).get('fields')) for p in (t.get('result_data') or {}).get('pages', []))} for t in tasks]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取任务列表失败: {str(e)}")


@app.get("/api/tasks/{task_id}")
async def get_task_info_api(task_id: str):
    """获取任务详细信息"""
    try:
        task_info = await task_queue.get_task_info(task_id)
        if not task_info:
            raise HTTPException(status_code=404, detail="任务不存在")
        return task_info
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取任务信息失败: {str(e)}")


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task_api(task_id: str):
    """取消任务"""
    try:
        success = await task_queue.cancel_task(task_id)
        if success:
            return {"message": "任务已取消", "task_id": task_id}
        else:
            raise HTTPException(status_code=400, detail="仅尚未处理的任务可以取消；已发出的模型请求无法撤回")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"取消任务失败: {str(e)}")


@app.post("/api/tasks/{task_id}/start")
async def start_task_api(task_id: str):
    """开始处理uploaded状态的任务"""
    try:
        success = await task_queue.start_task(task_id)
        if success:
            return {"message": "任务已开始处理", "task_id": task_id}
        else:
            raise HTTPException(status_code=400, detail="任务无法启动，可能已在处理中")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"启动任务失败: {str(e)}")


@app.delete("/api/tasks/{task_id}")
async def delete_task_api(task_id: str):
    """删除任务（从数据库和队列中彻底移除）"""
    raise HTTPException(409, "工作台使用可恢复的隐藏操作，请调用 visibility 接口")


@app.post("/api/tasks/{task_id}/pause")
async def pause_task_api(task_id: str):
    """暂停任务"""
    try:
        success = await task_queue.pause_task(task_id)
        if success:
            return {"message": "任务已暂停", "task_id": task_id}
        else:
            raise HTTPException(status_code=400, detail="暂停任务失败")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"暂停任务失败: {str(e)}")


@app.post("/api/tasks/{task_id}/resume")
async def resume_task_api(task_id: str):
    """恢复任务"""
    try:
        success = await task_queue.resume_task(task_id)
        if success:
            return {"message": "任务已恢复", "task_id": task_id}
        else:
            raise HTTPException(status_code=400, detail="恢复任务失败")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"恢复任务失败: {str(e)}")


@app.get("/api/tasks/queue/status")
async def get_queue_status_api():
    """获取队列状态"""
    try:
        status = await task_queue.get_queue_status()
        return status
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取队列状态失败: {str(e)}")


@app.get("/api/tasks/{task_id}/result")
async def get_task_result(task_id: str):
    """获取任务结果"""
    try:
        task_info = await task_queue.get_task_info(task_id)

        if not task_info:
            raise HTTPException(status_code=404, detail="任务不存在")


        result_data = task_info.get("result_data")
        if not result_data:
            raise HTTPException(status_code=404, detail="任务结果不存在")

        return result_data

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取任务结果失败: {str(e)}")


@app.post("/api/export/merged")
async def export_merged_document(
    job_id: str = Body(...),
    pages: List[Dict[str, Any]] = Body(...),
    target_lang: str = Body("en")
):
    """
    导出合并后的多页文档 (新增)
    按照规范格式生成正式译文
    """
    try:
        valid_job_dir(job_id)
        pages = [page for page in pages if page.get("status") == "success"]
        if not pages:
            raise HTTPException(status_code=400, detail="没有可导出的成功页面")

        # 获取文档类型
        first_page = pages[0]
        doc_type_cn = first_page.get("doc_type_cn") or "证件"

        # 生成输出文件名
        output_filename = f"译文_{job_id}_{uuid.uuid4().hex[:8]}.docx"
        output_path = OUTPUT_DIR / output_filename

        # 使用多页处理器导出合并文档
        await asyncio.to_thread(multi_page_processor.export_merged_document, pages, output_path, target_lang)

        return FileResponse(
            output_path,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=output_filename
        )

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        print(f"导出合并文档失败: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"导出失败: {str(e)}")


@app.post("/api/export/ocr")
async def export_ocr_document(request: ExportRequest):
    """导出 OCR 原文文档（中文）"""
    try:
        output_path = OUTPUT_DIR / f"OCR原文_{request.doc_type_cn}_{request.job_id}.docx"

        # 创建简单的 OCR 原文文档
        doc = template_manager.create_simple_document(
            request.doc_type_cn,
            request.extracted_fields.get("fields", {}),
            include_translation=False
        )

        doc.save(output_path)

        return FileResponse(
            output_path,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=f"OCR原文_{request.doc_type_cn}_{request.job_id}.docx"
        )

    except Exception as e:
        import traceback
        print(f"导出 OCR 原文失败: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"导出失败: {str(e)}")


@app.post("/api/export/translation")
async def export_translation_document(request: ExportRequest):
    """导出译文文档（英文，按模板格式）"""
    try:
        # 1. 匹配模板
        match_result = await template_manager.find_best_template(
            request.doc_type,
            request.extracted_fields.get("fields", {}),
            "gemini-3.8-flash"
        )

        output_path = OUTPUT_DIR / f"译文_{request.doc_type_cn}_{request.job_id}.docx"

        if match_result.get("mode") == "use_existing":
            # 使用模板填充
            template_path = match_result.get("attachment_template")
            if template_path:
                doc = template_manager.fill_template(
                    template_path,
                    request.extracted_fields.get("fields", {}),
                    request.translations
                )
            else:
                # 创建简单文档
                doc = template_manager.create_simple_document(
                    request.doc_type_cn,
                    request.extracted_fields.get("fields", {}),
                    request.translations,
                    include_translation=True
                )
        else:
            # 生成新模板
            doc = template_manager.create_simple_document(
                request.doc_type_cn,
                request.extracted_fields.get("fields", {}),
                request.translations,
                include_translation=True
            )

        doc.save(output_path)

        return FileResponse(
            output_path,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=f"译文_{request.doc_type_cn}_{request.job_id}.docx"
        )

    except Exception as e:
        import traceback
        print(f"导出译文失败: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"导出失败: {str(e)}")


@app.get("/api/image/{job_id}/{page_num}")
async def get_image(job_id: str, page_num: int):
    """获取页面图片"""
    image_path = valid_job_dir(job_id) / f"page_{page_num}.png"

    if not image_path.exists():
        raise HTTPException(status_code=404, detail="图片不存在")

    return FileResponse(image_path)


@app.get("/api/config")
async def get_config():
    """获取配置信息"""
    return {
        "models": config["models"]["available"],
        "ocr_model": config["models"]["ocr"],
        "translation_model": config["models"]["translation"]
    }





@app.post("/api/tasks/{task_id}/visibility")
async def task_visibility(task_id: str, hidden: bool = Body(..., embed=True)):
    if not task_storage.get_task(task_id):
        raise HTTPException(404, "任务不存在")
    task_storage.set_hidden(task_id, hidden)
    return {"hidden": hidden}


@app.put("/api/tasks/{task_id}/result")
async def save_review(task_id: str, pages: List[Dict[str, Any]] = Body(..., embed=True)):
    task = task_storage.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if task['status'] not in ('completed', 'partial', 'failed', 'unknown'):
        raise HTTPException(409, "任务仍在处理，完成后再保存复核")
    result = task.get('result_data') or {}
    existing = result.get('pages', [])
    if [p.get('page_num') for p in pages] != [p.get('page_num') for p in existing]:
        raise HTTPException(400, "复核页码与原任务不一致")
    # Only editable content is accepted; page outcomes and provenance stay intact.
    for saved, edited in zip(existing, pages):
        for key in ('ocr_text', 'extracted_fields', 'translations'):
            if key in edited:
                saved[key] = edited[key]
    result['reviewed_at'] = __import__('datetime').datetime.now().isoformat()
    task_storage.save_task_result(task_id, result)
    return {"message": "复核修改已保存", "reviewed_at": result['reviewed_at']}

@app.post("/api/export/original")
async def export_original(job_id: str = Body(...), pages: List[Dict[str, Any]] = Body(...),
                          target_lang: str = Body("en")):
    valid_job_dir(job_id)
    pages = [p for p in pages if p.get('status') == 'success']
    if not pages:
        raise HTTPException(400, "没有可导出的成功页面")
    output = OUTPUT_DIR / f"原文_{job_id}_{uuid.uuid4().hex[:8]}.docx"
    def create():
        doc = Document()
        for i, page in enumerate(pages):
            if i:
                doc.add_page_break()
            doc.add_heading(f"第 {page['page_num']} 页 · 识别原文", 1)
            doc.add_paragraph(page.get('ocr_text', ''))
            fields = page.get('extracted_fields', {}).get('fields', {})
            if fields:
                doc.add_heading('复核字段', 2)
                table = doc.add_table(rows=0, cols=2)
                table.style = 'Table Grid'
                for key, field in fields.items():
                    cells = table.add_row().cells
                    cells[0].text = str(field.get('field_type', key) if isinstance(field, dict) else key)
                    cells[1].text = str(field.get('value', '') if isinstance(field, dict) else field)
        doc.save(output)
    await asyncio.to_thread(create)
    return FileResponse(output, filename=output.name,
                        media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')


@app.post("/api/tasks/{task_id}/retry-translation")
async def retry_task_translation(task_id: str):
    if not task_storage.get_task(task_id):
        raise HTTPException(404, "任务不存在")
    if not await task_queue.retry_translation(task_id):
        raise HTTPException(409, "没有可以单独重试的翻译页面，或任务仍在处理中")
    return {"task_id": task_id, "message": "已加入翻译队列，将复用已保存字段，不重新识别"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8092")), log_level="info")
