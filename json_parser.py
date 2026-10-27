"""Parse model JSON without deleting field content or issuing another API call."""
import json
import logging
import os
from pathlib import Path
import re
import tempfile

logger = logging.getLogger('ocr.processing')


def _without_comments(text):
    """Remove comments only outside strings; preserve line/column positions."""
    output = []
    i = 0
    in_string = escaped = False
    while i < len(text):
        char = text[i]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_string = False
            i += 1
        elif char == '"':
            in_string = True
            output.append(char)
            i += 1
        elif text.startswith('//', i):
            end = text.find('\n', i)
            end = len(text) if end == -1 else end
            output.extend(' ' * (end - i))
            i = end
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            if end == -1:
                output.append(text[i:])  # Do not silently accept an incomplete comment.
                break
            end += 2
            output.extend(c if c in '\r\n' else ' ' for c in text[i:end])
            i = end
        else:
            output.append(char)
            i += 1
    return ''.join(output)


def _escape_string_controls(text):
    """Escape literal U+0000..001F within strings, preserving their values."""
    output = []
    in_string = escaped = False
    for char in text:
        if in_string and not escaped and ord(char) < 32:
            output.append(json.dumps(char)[1:-1])
            continue
        output.append(char)
        if escaped:
            escaped = False
        elif in_string and char == '\\':
            escaped = True
        elif char == '"':
            in_string = not in_string
    return ''.join(output)


def log_response_metadata(response, context, *, model, max_tokens):
    """Keep finish/usage evidence without logging document content."""
    choices = getattr(response, 'choices', None) or []
    usage = getattr(response, 'usage', None)
    def get(obj, name):
        return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
    metadata = {
        'model': model, 'max_tokens': max_tokens,
        'finish_reason': get(choices[0], 'finish_reason') if choices else None,
        'response_id': getattr(response, 'id', None),
        'usage': {key: get(usage, key) for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
    }
    metadata['usage']['reasoning_tokens'] = get(get(usage, 'completion_tokens_details'), 'reasoning_tokens')
    logger.info('模型响应 | 方法: %s | %s', context, json.dumps(metadata, ensure_ascii=False))
    return metadata


def _truncation_hint(error, text, metadata):
    if not isinstance(error, json.JSONDecodeError):
        return ''
    reason = (metadata or {}).get('finish_reason')
    near_end = error.pos >= max(0, len(text.rstrip()) - 1)
    if reason == 'length':
        return '模型报告输出已达到长度限制；建议增加 max_tokens 或减少单次字段数量。'
    if error.msg.startswith('Unterminated string') or (near_end and error.msg.startswith('Expecting')):
        return 'JSON 疑似截断：返回内容不完整；请结合 finish_reason 和 token 用量判断原因，必要时增加 max_tokens 或减少单次字段数量。'
    return ''


def parse_json_safe(text: str, context: str = 'unknown', *, response_metadata=None) -> dict:
    original = text
    cleaned = text.strip().lstrip('\ufeff') if isinstance(text, str) else ''
    fence = re.fullmatch(r'```(?:json)?[ \t]*\r?\n?(.*?)\r?\n?```', cleaned,
                         flags=re.IGNORECASE | re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    cleaned = _without_comments(cleaned)
    try:
        try:
            result = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            if not exc.msg.startswith('Invalid control character'):
                raise
            cleaned = _escape_string_controls(cleaned)
            result = json.loads(cleaned)
            logger.warning('JSON 控制字符已本地转义 | 方法: %s', context)
        if not isinstance(result, dict):
            raise ValueError('JSON 顶层必须是对象')
        return result
    except (json.JSONDecodeError, ValueError) as exc:
        hint = _truncation_hint(exc, cleaned, response_metadata)
        if hint:
            logger.warning('%s | 方法: %s | finish_reason: %s | max_tokens: %s',
                           hint, context, (response_metadata or {}).get('finish_reason'),
                           (response_metadata or {}).get('max_tokens'))
        safe_context = re.sub(r'[^A-Za-z0-9_-]', '_', str(context))[:64] or 'unknown'
        try:
            fd, name = tempfile.mkstemp(prefix=f'json_fail_{safe_context}_', suffix='.txt')
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                output.write(f'Context: {context}\nError: {exc}\nHint: {hint}\nResponse metadata: {json.dumps(response_metadata or {}, ensure_ascii=False)}\n\n=== Original ===\n{original}\n\n=== Cleaned ===\n{cleaned}')
            location = f'诊断文件已保存: {Path(name)}'
        except OSError:
            location = '诊断文件保存失败，请检查临时目录权限'
        logger.error('JSON 解析失败 | 方法: %s | %s', context, location)
        raise RuntimeError(f'JSON 解析失败 [{context}]: {exc}\n' + (hint + '\n' if hint else '') + location) from exc
