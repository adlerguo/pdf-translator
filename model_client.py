"""Shared bounded model timeouts; SDK retries disabled (extractor has its own one-retry connection policy)."""
import os
import asyncio
import logging
import time
import urllib.request
import urllib.error
from urllib.parse import urlsplit
import openai
try:
    import httpx2 as httpx
except ImportError:
    import httpx

_route_cache = {}
_route_lock = asyncio.Lock()


def _probe_route(base_url, direct):
    """Probe without credentials or model generation; 401 proves API reachability."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if direct else urllib.request.build_opener()
    try:
        with opener.open(base_url + '/models', timeout=5) as response:
            return response.status == 200
    except urllib.error.HTTPError as exc:
        return exc.code == 401
    except (OSError, urllib.error.URLError):
        return False


class RoutedToapisClient(openai.AsyncOpenAI):
    async def request(self, *args, **kwargs):
        # Resolve before SDK constructs the request; share the short-lived result
        # between OCR, extraction and translation clients.
        async with _route_lock:
            cached = _route_cache.get('toapis')
            if not cached or time.monotonic() - cached[0] > 60:
                selected = None
                for endpoint, direct in [('https://toapis.cn/v1', True), ('https://toapis.com/v1', False)]:
                    if await asyncio.to_thread(_probe_route, endpoint, direct):
                        selected = (endpoint, direct)
                        break
                if selected is None:
                    raise openai.APIConnectionError(request=httpx.Request('GET', 'https://toapis.cn/v1/models'), message='ToAPIs 国内和国际入口连接探测均失败')
                cached = (time.monotonic(), *selected)
                _route_cache['toapis'] = cached
                logging.getLogger('ocr.processing').info('网络路由 | ToAPIs | 地址: %s | 国内直连: %s', *selected)
            endpoint, direct = cached[1:]
            if str(self.base_url).rstrip('/') != endpoint:
                await self._client.aclose()
                self._client = openai.DefaultAsyncHttpxClient(trust_env=not direct)
                self.base_url = endpoint
        try:
            return await super().request(*args, **kwargs)
        except openai.APIConnectionError:
            _route_cache.pop('toapis', None)
            raise


def create_async_client(api_key, base_url):
    read_seconds = max(30.0, float(os.getenv('OCR_MODEL_READ_TIMEOUT', '300')))
    client_class = RoutedToapisClient if urlsplit(base_url).hostname in {'toapis.com', 'api.toapis.com', 'toapis.cn', 'api.toapis.cn'} else openai.AsyncOpenAI
    return client_class(
        api_key=api_key, base_url=base_url, max_retries=0,
        timeout=openai.Timeout(read_seconds, connect=20.0, read=read_seconds,
                               write=60.0, pool=30.0))
