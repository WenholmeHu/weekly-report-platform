"""LLM 调用封装。

这里只做两件事：
1. 把提示词发给兼容 OpenAI Chat Completions 的接口
2. 从响应里抽出上层真正需要的文本
"""

from __future__ import annotations

import json

import httpx

from weekly_report_platform.infrastructure.config import LLMSettings, load_project_env
from weekly_report_platform.infrastructure.logging import logger


class LLMServiceError(RuntimeError):
    """Raised when the LLM API request fails or returns an unusable response."""


def _extract_text_from_content(content: object) -> str:
    """兼容字符串 content 和 text block 数组两种常见格式。"""
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                text_parts.append(block["text"])
        merged_text = "".join(text_parts).strip()
        if merged_text:
            return merged_text

    raise LLMServiceError("LLM response does not contain text content")


def _extract_response_text(result: object) -> str:
    """从完整响应体中提取 message.content 文本。"""
    if not isinstance(result, dict):
        raise LLMServiceError("LLM response payload must be a JSON object")

    choices = result.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMServiceError("LLM response does not contain choices")

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise LLMServiceError("LLM choice payload is invalid")

    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise LLMServiceError("LLM response does not contain a message object")

    return _extract_text_from_content(message.get("content"))


class LLMClient:
    def __init__(self) -> None:
        load_project_env()
        settings = LLMSettings()
        self.url = settings.url
        self.headers = {
            "Authorization": settings.authorization,
            "Content-Type": "application/json",
        }
        self.model = settings.model
        self.max_tokens = settings.max_tokens
        self.temperature = settings.temperature
        self.stream = settings.stream
        self.n = settings.n

    async def acall(self, prompt: str) -> str:
        """发送一次异步模型请求并返回文本结果。"""
        if not self.url:
            raise LLMServiceError("LLM_URL is not configured")

        payload = json.dumps(
            {
                "max_tokens": self.max_tokens,
                "messages": [{"content": prompt, "role": "user"}],
                "model": self.model,
                "n": self.n,
                "stream": self.stream,
                "temperature": self.temperature,
            }
        )

        try:
            # 当前项目并发不高，按次创建 client 更直观，
            # 不再额外拆出复杂的连接池生命周期管理。
            async with httpx.AsyncClient(timeout=180.0) as client:
                response = await client.post(self.url, headers=self.headers, content=payload)
                response.raise_for_status()

            return _extract_response_text(response.json())
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else "unknown"
            response_text = exc.response.text[:500].strip() if exc.response is not None else ""
            message = f"HTTP {status_code} calling LLM API"
            if response_text:
                message = f"{message}: {response_text}"
            logger.error(message)
            raise LLMServiceError(message) from exc
        except httpx.RequestError as exc:
            message = f"LLM API network error; check LLM_URL, VPN/company network, and service availability: {exc}"
            logger.error(message)
            raise LLMServiceError(message) from exc
        except ValueError as exc:
            message = f"Failed to decode LLM response JSON: {exc}"
            logger.error(message)
            raise LLMServiceError(message) from exc


llm_client = LLMClient()
