# 文档工作台：OCR、翻译与 Word 导出

一个本机运行的文档处理工作台。上传 PDF 或图片，调用视觉模型识别文字，提取结构化字段并翻译，最后对照原图复核、导出可编辑 Word。

页面采用“左侧选择配置、中间上传与提交、右侧查看任务”的布局。前端为原生 HTML/CSS/JavaScript，无需 Node.js 构建；后端为 FastAPI，任务与结果存储在 SQLite。

本文依据 **2026-10-27 的实际运行代码和环境**整理。当前正式入口是 **`app_v2.py`**，不是旧版 `app.py`。

## 需要准备什么

| 项目 | 要求 |
| --- | --- |
| Python | 推荐 **3.12**；当前验证环境为 macOS + Python **3.12.13** |
| 系统工具 | **Poppler**，其中 `pdftoppm` 和 `pdfinfo` 必须在 PATH 中，用于 PDF 分页转图 |
| Python 依赖 | 安装本项目 `requirements.lock.txt`；不需要自己部署数据库 |
| 模型服务 | 可访问的 **OpenAI Chat Completions 兼容接口**、自己的 API Key 和可用额度 |
| OCR 模型 | 必须支持图片输入（`image_url` / Base64 图片） |
| 提取与翻译模型 | 支持文本输入，并能按提示词稳定返回 JSON；可以与 OCR 使用同一模型 |
| 磁盘与权限 | 项目目录可写日志；上传、结果、SQLite 目录可写。PDF 转为图片后占用会明显增加 |
| 浏览器 | 支持 ES Modules、Fetch、localStorage、`<dialog>` 等能力的现代浏览器 |

**不需要本地 GPU，不需要安装 Office，不需要 Redis、MySQL 或前端构建工具。** OCR 和翻译由已配置的远端模型服务完成，不是离线识别。Node.js 只用于可选的前端控制器测试。

## 快速开始：macOS / Linux

先克隆仓库（私有仓库需要已获授权的 GitHub 账号），或者下载源码 ZIP 后解压：

```sh
git clone https://github.com/adlerguo/pdf-translator.git
cd pdf-translator
```

以下命令均在 `pdf-translator` 目录执行。不要复制他人电脑上的 `venv`，应在自己的机器上新建。

### 1. 安装 Python 与 Poppler

先准备 Python 3.12，并确认 `python3 --version` 显示预期版本。

macOS（已安装 Homebrew）：

```sh
brew install python@3.12 poppler
```

Ubuntu / Debian：

```sh
sudo apt update
sudo apt install poppler-utils
```

Linux 的 Python 3.12 和 `venv` 安装方式取决于发行版。若系统提供 `python3.12-venv`，需要一并安装。不要为了本项目替换系统 Python。

确认 PDF 工具可用：

```sh
pdftoppm -v
pdfinfo -v
```

### 2. 创建环境并安装依赖

```sh
python3.12 -m venv venv
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r requirements.lock.txt
```

`requirements.txt` 列出直接依赖，`requirements.lock.txt` 记录当前运行环境的完整版本。旧依赖清单已更新，不再使用之前的 OpenAI 1.54.3 等旧版本。

> 版本快照来自实际运行环境，不等于已在所有系统上完成全新安装验证；当前已验证平台是 macOS。Linux、Windows 的命令用于移植，仍需在目标机器进行自检和一次实际任务验收。

### 3. 配置模型服务

```sh
cp config.example.json config.json
```

编辑 `config.json`，替换以下内容：

| 配置 | 填什么 |
| --- | --- |
| `api.base_url` | 提供商的 OpenAI 兼容 API 根地址，按提供商要求填写是否带 `/v1`；不要填网页地址或完整 `/chat/completions` 请求地址 |
| `api.api_key` | 你自己的密钥 |
| `models.ocr.name` | 实际可用的视觉模型 ID |
| `models.translation.name` | 实际可用的字段提取与翻译模型 ID |
| `models.available[].id` | 与上方默认模型 ID 对应，大小写和后缀需一致 |
| `models.available[].use_for` | 识别模型包含 `"ocr"`；提取与翻译模型包含 `"translation"` |
| `queue.max_concurrent_tasks` | 同时处理的文档数，默认 2；额度或限流较紧时设为 1 |

`name` / `id` 是发给 API 的真实模型标识，`display_name` 与目录中的 `name` 只是界面显示文字。若同一个模型兼任两种角色，可在 `available` 中保留一项，设置 `"use_for": ["ocr", "translation"]`。

示例配置使用占位符，不附带可用密钥或承诺可用的模型。示例里的其他结构可以保留，尤其是 `ocr.system_prompt`。

配置修改后需重启服务。当前程序从 **项目目录的 `config.json`** 读取 API 地址和密钥，不会自动读取 `.env`，也不读取 `OPENAI_API_KEY` / `OPENAI_BASE_URL`。

### 4. 自检并启动

```sh
./venv/bin/python check_environment.py
./venv/bin/python app_v2.py
```

浏览器打开：**http://127.0.0.1:8092**

macOS / Linux 也可运行：

```sh
./start.sh
```

`start.sh` 检查配置与 Poppler；没有 venv 时自动创建并安装锁定依赖，已有环境则直接使用。它不会自动打开浏览器。按 `Ctrl+C` 停止服务。

## Windows 安装

1. 安装 Python 3.12，并确认 Python Launcher（`py`）可用。
2. 安装可用于 Windows 的 Poppler，将含 `pdftoppm.exe`、`pdfinfo.exe` 的目录加入 PATH。打开新 PowerShell，运行 `pdftoppm -v` 与 `pdfinfo -v` 确认可用。
3. 在项目目录执行：

```powershell
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.lock.txt
Copy-Item config.example.json config.json
```

编辑配置后执行：

```powershell
.\venv\Scripts\python.exe check_environment.py
.\venv\Scripts\python.exe app_v2.py
```

打开同样的本机地址。Windows 不使用 `start.sh` / `restart.sh`；这里直接调用虚拟环境的 Python，无需激活脚本或修改 PowerShell 执行策略。

## 使用流程

1. 选择文字识别模型、字段提取与翻译模型、目标语言。
2. 点击或拖入 PDF / 图片，可多选。上传与 PDF 转换完成前禁止提交。
3. 点击“开始处理”。每份文件对应一个任务；服务接收后可以继续准备下一批。
4. 在右侧查看排队、处理、完成、部分完成、失败或待核对状态。
5. 点击“查看与复核”，对照原图修改字段并保存；下载原文 Word 或译文 Word。

当前限制：单文件 **50MB**，单份 PDF **100 页**，单批 **20 份文件**。图片格式包括 PNG、JPG/JPEG、WebP、BMP、TIFF/TIF、GIF；当前图片入口按单页处理，不提供多帧图片拆页。多张图片不会自动合并成一个任务。

## 环境变量

API 凭证仍在 `config.json`，下列变量只用于运行参数。示例使用绝对路径；设置后重启生效。

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `PORT` | `8092` | 执行 `python app_v2.py` 时的监听端口 |
| `OCR_MODEL_READ_TIMEOUT` | `300` 秒，最小 30 秒 | 等待模型响应的读取超时；连接超时 20 秒、写入超时 60 秒 |
| `OCR_DATA_DIR` | 项目目录 `data/` | SQLite 数据目录 |
| `OCR_UPLOAD_DIR` | 项目目录 `uploads/` | 原件与分页图片目录 |
| `OCR_OUTPUT_DIR` | 项目目录 `outputs/` | Word 输出目录 |
| `OCR_TEMPLATE_DIR` | 见下方说明 | 可选模板目录 |

macOS / Linux 示例：

```sh
export PORT=8095
export OCR_MODEL_READ_TIMEOUT=600
./venv/bin/python app_v2.py
```

PowerShell 示例：

```powershell
$env:PORT="8095"
$env:OCR_MODEL_READ_TIMEOUT="600"
.\venv\Scripts\python.exe app_v2.py
```

模型 SDK 自动重试已关闭。延长读取超时可以容忍较慢响应，不能解决无效密钥、额度不足、限流或上游宕机。

## 数据、恢复与迁移

| 位置 | 内容 | 是否应公开分享 |
| --- | --- | --- |
| `config.json` | API 地址、密钥与模型配置 | 否，分享 `config.example.json` |
| `data/tasks.db` | 任务、参数、原文与译文结果 | 否 |
| `uploads/` | 原始文件、分页图片与素材清单 | 否 |
| `outputs/` | Word 结果 | 否 |
| `processing.log` | 新增的处理阶段、耗时与错误类型 | 排查时按需提供 |
| `backups/`、其他旧日志 | 可能含历史业务材料或旧代码 | 否 |

- 刷新页面后可恢复当前浏览器的素材草稿；任务和已保存复核在服务端 SQLite 中。
- 关闭浏览器不影响运行中的服务。服务停止会停止本地处理；处理中任务重启后标为“待核对”，不自动重发模型请求。尚未开始的排队任务可以继续。
- 翻译失败但字段已保存时，可手动点“仅重试翻译”，不用重跑 OCR；该操作仍可能产生模型费用。
- 部分完成仅导出成功页面。请检查失败页，不要将文件已下载视为所有内容已完成。
- 隐藏任务不是取消或删除，可以恢复显示。

**备份前先停止服务**，一并备份数据库、uploads、outputs 和自己的配置。不要只复制数据库后就删除原件。

现有任务在配置中保存了分页图片的**绝对路径**。将旧数据迁往不同机器或不同目录，不是仅复制 `data/` 就能保证续跑；新用户通常应从空数据目录开始。需要迁移历史时，应保留原目录映射，或由维护者校正任务中的文件路径后再运行。

## Word 模板是否必需

不必需。当前主流程使用 `document_formatter.py` 导出标准 Word，不依赖原作者电脑上的模板文件。

模板目录优先使用 `OCR_TEMPLATE_DIR`；未设置时，若项目上一级存在 `模板/` 则兼容使用该目录，否则使用项目内 `templates/`。保留的模板匹配接口扫描第一层文件名包含“公证书”或“附件”的 `.docx`。仅放入模板不等于主界面会自动按该模板出件。

## 自检与测试

环境检查不调用模型、不打印密钥：

```sh
./venv/bin/python check_environment.py
```

隔离测试使用临时文件和模拟模型，不运行真实付费识别：

```sh
./venv/bin/python -m unittest discover -s tests -v
node tests/test_frontend.cjs
```

Python 测试需要 HTTP 客户端；锁定环境包含 `httpx2`，测试也兼容已安装的 `httpx`。Node.js 不参与启动和实际文档处理。

自检和模拟测试都不能证明模型当前有额度或可用。首次正式验收建议用一张不含敏感信息的小图片，确认识别、翻译、复核、Word 下载完整跑通，再处理业务文件。

## 常见问题

| 问题 | 排查方式 |
| --- | --- |
| 缺少 `config.json` | 复制配置示例，填入自己的地址、密钥、模型 ID |
| `ModuleNotFoundError` | 确认使用的是 venv 里的 Python，并在同一环境安装依赖 |
| `Unable to get page count` / 找不到 Poppler | 确认 `pdfinfo`、`pdftoppm` 都在运行服务的进程 PATH 中；Windows 修改 PATH 后重新打开终端 |
| `401` / `403` | 核对密钥、提供商地址、账号权限 |
| 模型不存在 / `404` | 核对真实模型 ID 与 API 根地址；相似显示名称不代表同一模型 |
| `429` | 核对额度与并发限制，将文档并发设为 1 后再试 |
| `Request timed out` | 查看任务逐页错误与 `processing.log`；可适度增加读取超时。翻译阶段失败时优先仅重试翻译 |
| 页面连不上 | 检查终端是否仍在运行、端口是否被占用；默认地址是 HTTP 而不是 HTTPS |
| 其他电脑访问不了 | 默认仅监听 `127.0.0.1`，这是本机地址；远程部署见 DEPLOYMENT.md |
| Word 没包含全部页 | 查看是否为部分完成或存在没有结构化字段的页面，回到复核界面核对 |

## 项目结构

```text
pdf-translator/
├── app_v2.py                 # 当前服务入口
├── model_client.py           # 异步模型客户端与超时
├── multi_page_processor.py   # 多页识别与处理
├── intelligent_extractor.py  # 字段提取与翻译
├── task_queue.py             # 本地任务调度与恢复
├── task_storage.py           # SQLite 持久化
├── document_formatter.py     # 标准 Word 排版
├── template_manager.py       # 可选模板兼容功能
├── config.example.json       # 不含真实密钥的配置示例
├── requirements.txt          # 直接依赖
├── requirements.lock.txt     # 当前运行环境版本快照
├── check_environment.py      # 只读环境自检
├── start.sh / restart.sh     # macOS / Linux 启动辅助
├── static/workbench.*        # 当前前端，无构建步骤
├── templates/                # 可选模板
└── tests/                    # 隔离测试
```

源码包不需要附带 `venv`、真实配置、数据库、上传文件、导出结果、旧日志或旧前端版本。

## 源码与数据边界

本仓库仅包含当前入口 `app_v2.py`、处理模块、工作台前端、依赖清单、配置示例、运行文档和隔离测试。不包含旧版 `app.py`，也不包含真实 API 密钥、任务数据库、原件、Word 结果、venv 或开发电脑的 Git 历史。

使用自己的 `config.json`，不要提交该文件。`.gitignore` 用于防止运行数据误入版本控制；分享修改前仍需检查文件清单。项目尚未指定开源许可证。
