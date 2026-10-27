"""Read-only preflight: no API calls, imports of the application, or secret output."""
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent

def main():
    failures = []
    def check(label, passed, hint=''):
        print(f"[{'OK' if passed else 'FAIL'}] {label}" + (f'：{hint}' if not passed and hint else ''))
        if not passed: failures.append(label)
    check('Python >= 3.12', sys.version_info >= (3, 12), '推荐 Python 3.12，当前验证版本为 3.12.13')
    print(f'[INFO] Python {sys.version.split()[0]} / {sys.platform}')
    for line in (ROOT / 'requirements.txt').read_text().splitlines():
        if not line or line.startswith('#'): continue
        package, expected = line.split('==')
        try: actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError: actual = None
        check(f'依赖 {package}', actual is not None, '安装 requirements.lock.txt')
        if actual and actual != expected: print(f'[WARN] {package} 当前 {actual}，记录版本 {expected}')
    for tool in ['pdftoppm', 'pdfinfo']:
        check(f'Poppler / {tool}', shutil.which(tool) is not None, '安装 Poppler 并加入 PATH')
    config_path = ROOT / 'config.json'
    check('config.json 存在', config_path.is_file(), '从 config.example.json 复制并修改')
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding='utf-8'))
            url = urlsplit(config.get('api', {}).get('base_url', ''))
            check('API 地址已填写', url.scheme in ('https', 'http') and bool(url.hostname) and 'YOUR-' not in url.netloc,
                  '填写提供商实际的 OpenAI 兼容 API 根地址')
            key = config.get('api', {}).get('api_key', '')
            check('API 密钥已填写', bool(key) and key != 'REPLACE_WITH_YOUR_API_KEY', '填写自己的密钥；脚本不会输出密钥')
            models = config.get('models', {})
            available = models.get('available', [])
            for role, config_key in [('ocr', 'ocr'), ('translation', 'translation')]:
                name = models.get(config_key, {}).get('name', '')
                check(f'{role} 默认模型与目录匹配', bool(name) and not name.startswith('YOUR_') and
                      any(m.get('id') == name and role in m.get('use_for', [role]) for m in available),
                      '默认模型 name 与 available 中 id 一致，use_for 包含相应角色')
            check('OCR 提示词存在', bool(config.get('ocr', {}).get('system_prompt')))
        except (ValueError, TypeError, AttributeError):
            check('配置结构有效', False, '检查 JSON 语法和配置示例，未输出配置内容')
    check('项目目录可写（日志）', os.access(ROOT, os.W_OK))
    for env, default in [('OCR_DATA_DIR', 'data'), ('OCR_UPLOAD_DIR', 'uploads'), ('OCR_OUTPUT_DIR', 'outputs')]:
        target = Path(os.getenv(env, str(ROOT / default))).expanduser()
        while not target.exists() and target != target.parent: target = target.parent
        check(f'{env} 路径可写', os.access(target, os.W_OK))
    print('\n自检不调用模型，不验证额度、模型在线状态或输出质量。')
    return 1 if failures else 0

if __name__ == '__main__':
    raise SystemExit(main())
