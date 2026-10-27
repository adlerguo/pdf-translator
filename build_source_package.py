"""Build a shareable source ZIP from an explicit allowlist, excluding all user data."""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent
FILES = [
    'README.md', 'CHANGELOG.md', 'DEPLOYMENT.md', 'config.example.json', '.gitignore',
    'requirements.txt', 'requirements.lock.txt', 'check_environment.py',
    'build_source_package.py', 'start.sh', 'restart.sh',
    'app_v2.py', 'model_client.py', 'json_parser.py', 'document_language.py', 'task_storage.py', 'task_queue.py',
    'multi_page_processor.py', 'intelligent_extractor.py',
    'document_formatter.py', 'template_manager.py',
    'static/workbench.html', 'static/workbench.css', 'static/workbench.js',
    'templates/README.md', 'tests/test_workbench.py', 'tests/test_document_language.py', 'tests/test_json_and_retry.py', 'tests/test_frontend.cjs'
]

def main():
    missing = [name for name in FILES if not (ROOT / name).is_file()]
    if missing:
        raise SystemExit('Missing files: ' + ', '.join(missing))
    target = ROOT / 'dist' / 'pdf-translator-source.zip'
    target.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(ROOT / name, 'pdf-translator/' + name)
    print(target)
    print(f'{len(FILES)} allowlisted files; private config, Git history and business data excluded.')

if __name__ == '__main__':
    main()
