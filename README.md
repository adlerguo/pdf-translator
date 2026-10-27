# PDF Translator · 文档识别与翻译工作台

将 PDF 和图片转换为可复核、可编辑的 Word 文档。在浏览器中完成文件上传、文字识别、字段提取、翻译与人工校对，适用于证件、证明及其他扫描文档的整理与翻译。

项目采用 FastAPI、SQLite 和原生 JavaScript，可在本机或受控服务器环境运行，无需前端构建、独立数据库或本地 GPU。文字识别与翻译通过用户配置的远程模型 API 完成。

[快速开始](#快速开始) · [模型配置](#模型配置) · [使用方法](#使用方法) · [服务器部署](#服务器部署) · [常见问题](#常见问题) · [版本记录](CHANGELOG.md)

## 功能

- **PDF 与图片上传**：支持多文件提交，PDF 自动分页处理。
- **识别与翻译**：通过视觉模型识别原文，提取结构化字段，再翻译为目标语言。
- **对照复核**：对照原图检查和修改字段，保存校对结果。
- **Word 导出**：下载原文或译文 Word，标题、字段标签等固定文字随目标语言变化，支持简体与繁体中文。
- **任务管理**：查看排队、处理、完成与失败状态，任务及结果保存在本地 SQLite。
- **故障诊断**：保留失败阶段前已获得的结果，提供连接重试、响应解析与阶段耗时日志。

> 文档内容会发送至配置的模型服务。处理前请确认该服务符合你的数据使用要求。识别与翻译结果需要人工复核；部分完成的任务仅导出成功页面。

## 运行要求

| 组件 | 要求 |
| --- | --- |
| Python | 推荐 3.12 |
| Poppler | `pdfinfo` 和 `pdftoppm` 可从终端调用，用于 PDF 分页转图 |
| 模型 API | OpenAI Chat Completions 兼容服务，具备有效密钥和可用额度 |
| OCR 模型 | 支持 `image_url` / Base64 图片输入 |
| 提取与翻译模型 | 支持文本输入与 `response_format={"type":"json_object"}` |
| 浏览器 | 支持 ES Modules、Fetch、localStorage 和 `<dialog>` 的现代浏览器 |
| 存储 | 配置、日志、数据库、上传及输出目录具备相应读写权限 |

不需要安装 Microsoft Office、Redis、MySQL 或 Node.js。Node.js 仅用于可选的前端测试。

已验证运行平台为 macOS / Python 3.12。Linux、Windows 安装步骤见下文，目标环境仍需完成依赖检查与端到端验证。`requirements.lock.txt` 是依赖版本快照，跨平台安装时需确认相应版本可用。

## 快速开始

### 1. 获取源码

```sh
git clone https://github.com/adlerguo/pdf-translator.git
cd pdf-translator
```

也可下载源码 ZIP 后解压。后续命令均在项目目录执行；受限仓库需使用具有访问权限的 GitHub 账号。

### 2. 安装系统依赖

**macOS**（使用 Homebrew）：

```sh
brew install python@3.12 poppler
```

**Ubuntu / Debian**：

```sh
sudo apt update
sudo apt install poppler-utils
```

Linux 还需安装 Python 3.12 及对应的 `venv` 组件，具体包名取决于发行版。

确认工具可用：

```sh
python3.12 --version
pdfinfo -v
pdftoppm -v
```

### 3. 安装 Python 依赖

```sh
python3.12 -m venv venv
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r requirements.lock.txt
```

`requirements.txt` 列出直接依赖，`requirements.lock.txt` 提供完整依赖版本快照。请在目标机器创建虚拟环境，不要复制其他机器的 `venv`。

### 4. 创建配置

```sh
cp config.example.json config.json
```

在 `config.json` 中填写 API 地址、密钥及模型 ID，配置字段见[模型配置](#模型配置)。

### 5. 检查并启动

```sh
./venv/bin/python check_environment.py
./venv/bin/python app_v2.py
```

打开 **[http://127.0.0.1:8092](http://127.0.0.1:8092)**。

macOS / Linux 也可使用 `./start.sh` 启动。该脚本检查配置与 Poppler，并在缺少虚拟环境时创建环境、安装依赖。按 `Ctrl+C` 停止服务。

首次使用建议上传一张不含敏感信息的小图片，确认识别、翻译、复核和 Word 下载流程完整可用。

### Windows

安装 Python 3.12 和 Windows 版 Poppler，将含 `pdfinfo.exe`、`pdftoppm.exe` 的目录加入 PATH。重新打开 PowerShell，确认两个命令可用后，在项目目录执行：

```powershell
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.lock.txt
Copy-Item config.example.json config.json
```

完成模型配置后启动：

```powershell
.\venv\Scripts\python.exe check_environment.py
.\venv\Scripts\python.exe app_v2.py
```

访问同一浏览器地址。Windows 直接使用上述 Python 命令，无需运行 Shell 脚本。

## 模型配置

配置文件为项目根目录的 `config.json`，完整结构见 [config.example.json](config.example.json)。

| 字段 | 说明 |
| --- | --- |
| `api.base_url` | 服务商提供的 OpenAI 兼容 API 根地址，例如 `https://your-provider.example/v1`；不要填写控制台网页或完整的 `/chat/completions` 地址 |
| `api.api_key` | 用户自己的 API 密钥 |
| `models.ocr.name` | 默认视觉模型的真实 ID |
| `models.translation.name` | 默认字段提取与翻译模型的真实 ID |
| `models.available[].id` | 可选模型的真实 ID，应包含上述默认模型 |
| `models.available[].name` | 模型在界面中的显示名称 |
| `models.available[].use_for` | `"ocr"` 用于识别，`"translation"` 用于字段提取与翻译 |
| `queue.max_concurrent_tasks` | 同时处理的文档数，默认 2 |

一个支持图片和结构化输出的模型可以兼任两种角色，在 `use_for` 中填写 `["ocr", "translation"]`。默认模型的 `name` 是接口 ID，`display_name` 是显示名称；模型 ID 的大小写与后缀应与服务商一致。

配置示例中的模型和密钥均为占位符，需自行替换。保留 `ocr.system_prompt` 等其他配置结构即可。程序读取 `config.json`，不会自动读取 `.env`、`OPENAI_API_KEY` 或 `OPENAI_BASE_URL`。修改配置后需重启服务。

### ToAPIs 网络路由

使用 ToAPIs 官方域名时，共用异步模型客户端自动选择网络入口：

1. 优先探测 `https://toapis.cn/v1`，可达时直接连接，不使用环境代理。
2. 国内入口不可达时探测 `https://toapis.com/v1`，国际入口可使用环境代理。
3. 两个入口均不可达时返回连接错误。

适用域名为 `toapis.com`、`api.toapis.com`、`toapis.cn`、`api.toapis.cn`，其他服务沿用原配置地址。探测访问 `/models`，不携带密钥或生成内容；HTTP 200 / 401 表示入口可达。结果缓存 60 秒，模型连接异常后清除缓存，下次调用重新探测。路由记录见 `processing.log`。

入口连通不代表模型权限、额度及生成请求均可用。

## 使用方法

1. 选择 OCR 模型、字段提取与翻译模型、目标语言。
2. 点击或拖入 PDF / 图片，等待上传和 PDF 转换完成。
3. 点击“开始处理”，在任务列表查看进度。
4. 打开“查看与复核”，对照原图检查并保存字段。
5. 下载原文 Word 或译文 Word。

| 上传项目 | 限制 |
| --- | --- |
| 单文件大小 | 50 MB |
| 单份 PDF 页数 | 100 页 |
| 单批文件数 | 20 份 |
| 图片格式 | PNG、JPG/JPEG、WebP、BMP、TIFF/TIF、GIF |

图片入口按单页处理，不拆分多帧图片；多张图片分别创建任务，不自动合并。

### 失败与恢复

- 原文、结构化字段和翻译是独立处理阶段。看到原文代表 OCR 已成功，不代表后续步骤已完成。
- 类型识别、智能字段提取、通用字段提取及字段翻译遇到连接错误时，等待 2 秒后最多重试一次。读取超时、HTTP 权限/额度错误和 JSON 解析错误不触发该连接重试；OCR 未增加此自动重试。
- 翻译失败且字段已保存时，可选择“仅重试翻译”，复用已有字段。重新调用模型可能产生费用。
- 部分完成仅导出成功页面，请逐页核对遗漏。
- 服务重启后，处理中任务标为待核对，不自动重新发送；尚未执行的排队任务可恢复。隐藏任务可以恢复显示，隐藏不等于取消或删除。

## 服务器部署

完成安装与配置后，以单进程运行：

```sh
./venv/bin/python -m uvicorn app_v2:app --host 127.0.0.1 --port 8092 --workers 1
```

**仅运行一个 worker、一个服务实例。** 任务调度在进程内完成，多进程共享 SQLite 可能导致重复执行。生产运行不要使用 `--reload`。

默认仅允许本机访问。项目未提供登录、用户隔离或 API 访问控制；远程访问应使用 SSH 转发，或配置带身份认证和 HTTPS 的反向代理。共享部署前需明确密钥与文档数据的访问范围。

进程托管、容器注意事项和迁移步骤见 [DEPLOYMENT.md](DEPLOYMENT.md)。

### 运行参数

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `8092` | 使用 `python app_v2.py` 启动时的端口；Uvicorn 命令使用 `--port` |
| `OCR_MODEL_READ_TIMEOUT` | `300` 秒 | 模型读取超时，最小 30 秒；连接超时 20 秒、写入超时 60 秒 |
| `OCR_DATA_DIR` | `data/` | SQLite 数据目录 |
| `OCR_UPLOAD_DIR` | `uploads/` | 原件与分页图片目录 |
| `OCR_OUTPUT_DIR` | `outputs/` | Word 输出目录 |
| `OCR_TEMPLATE_DIR` | `templates/` 或兼容目录 | 可选模板位置，详见下文 |

默认数据目录相对于项目目录。自定义目录建议使用绝对路径，并确保服务用户具有读写权限。例如：

```sh
PORT=8095 OCR_MODEL_READ_TIMEOUT=600 ./venv/bin/python app_v2.py
```

调整文档并发不会等比例提升吞吐量，还受全局页面并发与模型服务限制影响。延长超时也不能解决额度不足、限流或上游故障。

## 数据与备份

| 位置 | 内容 |
| --- | --- |
| `config.json` | API 密钥、地址与模型配置 |
| `data/tasks.db` | 任务参数、原文、字段与译文结果 |
| `uploads/` | 上传原件、分页图片和素材清单 |
| `outputs/` | 导出 Word |
| `processing.log` | 阶段耗时、路由、重试及响应诊断信息 |

这些运行文件不应提交到仓库。JSON 解析失败时，系统临时目录还会生成 `json_fail_*.txt`，包含模型原始响应与诊断信息，可能含文档内容；分享日志前应检查并脱敏。

备份时先停止服务，再一并保存配置、数据库、上传及输出目录。历史任务包含分页图片的绝对路径，跨机器迁移时需保留目录映射或校正路径，仅复制数据库无法保证恢复。

### Word 模板

标准 Word 导出不要求额外模板。可选模板目录由 `OCR_TEMPLATE_DIR` 指定；未设置时，优先兼容项目上一级的 `模板/`，否则使用项目内 `templates/`。

模板匹配接口扫描第一层名称含“公证书”或“附件”的 `.docx` 文件；添加模板不会自动改变主界面的标准出件流程。

## 常见问题

| 问题 | 处理方式 |
| --- | --- |
| 缺少 `config.json` | 复制 `config.example.json`，填写服务地址、密钥和模型 ID |
| `ModuleNotFoundError` | 使用虚拟环境 Python，并在同一环境安装依赖 |
| 无法获取 PDF 页数 | 检查 `pdfinfo`、`pdftoppm` 是否在服务进程的 PATH 中；修改 PATH 后重新打开终端 |
| `401` / `403` | 检查密钥、API 地址及账号权限 |
| 模型不存在 / `404` | 核对真实模型 ID 及接口根地址 |
| `429` | 检查额度与限流，降低 `queue.max_concurrent_tasks` 后重启 |
| `Connection error` | 查看失败阶段和路由日志，确认 API 与代理可达；ToAPIs 会按上述规则探测入口 |
| `Request timed out` | 查看阶段耗时，按需调整读取超时；字段已保存时可仅重试翻译 |
| JSON 解析失败 | 查看任务提供的诊断文件与 `finish_reason`；模型可能返回不完整或不符合格式的内容 |
| Word 缺页 | 检查任务是否部分完成，逐页核对失败原因 |
| 其他电脑无法打开页面 | `127.0.0.1` 仅代表本机；参阅服务器部署说明 |

结构化输出使用 JSON 模式，但仍可能因长度限制等原因返回不完整内容。程序保留解析检查与结束原因诊断，不将补齐括号后的残缺响应视为成功结果。

## 开发与测试

```sh
./venv/bin/python check_environment.py
./venv/bin/python -m unittest discover -s tests -v
node tests/test_frontend.cjs
```

环境检查不调用模型、不输出密钥。自动化测试使用模拟模型和临时任务数据；前端测试需要 Node.js。自检和测试通过不代表外部模型当前具备额度或服务可用性。

```text
pdf-translator/
├── app_v2.py                 # FastAPI 服务入口
├── model_client.py           # 异步客户端、超时与网络路由
├── multi_page_processor.py   # 多页文档处理
├── intelligent_extractor.py  # 类型判断、字段提取与翻译
├── json_parser.py            # JSON 解析与响应诊断
├── document_language.py      # 语言代码与本地化文字
├── document_formatter.py     # Word 排版与导出
├── task_queue.py             # 任务调度与恢复
├── task_storage.py           # SQLite 存储
├── config.example.json       # 配置示例
├── static/workbench.*        # 浏览器工作台
└── tests/                    # 自动化测试
```

项目不提供 `python app_v2.py process <PDF>` 处理子命令，文档处理通过工作台或 HTTP API 发起。

使用 `python3 build_source_package.py` 可生成按源码允许清单打包的 ZIP，排除密钥、数据库、原件、结果和虚拟环境。发布记录见 [CHANGELOG.md](CHANGELOG.md)。

## 许可证

仓库尚未指定开源许可证。
