#!/bin/bash
set -e
cd "$(dirname "$0")"
if [ ! -f config.json ]; then
    echo '缺少 config.json。请复制 config.example.json 为 config.json，并填写 API 地址、密钥和模型 ID。'
    exit 1
fi
if ! command -v pdftoppm >/dev/null || ! command -v pdfinfo >/dev/null; then
    echo '缺少 Poppler。macOS: brew install poppler；Ubuntu/Debian: sudo apt install poppler-utils'
    exit 1
fi
if [ ! -x venv/bin/python ]; then
    python3 -m venv venv
    venv/bin/python -m pip install -r requirements.lock.txt
fi
echo "文档工作台：http://127.0.0.1:${PORT:-8092}"
exec venv/bin/python app_v2.py
