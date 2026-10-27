"""Shared bounded model timeouts; never automatically replay billable calls."""
import os
import openai


def create_async_client(api_key, base_url):
    read_seconds = max(30.0, float(os.getenv('OCR_MODEL_READ_TIMEOUT', '300')))
    return openai.AsyncOpenAI(
        api_key=api_key, base_url=base_url, max_retries=0,
        timeout=openai.Timeout(read_seconds, connect=20.0, read=read_seconds,
                               write=60.0, pool=30.0))
