"""多模态 LLM 调用封装。

支持文本 + 图片输入，使用独立的外部 vision 网关（VisionSettings）。
HundSun 内部 LLM 网关只接受纯字符串 content，不支持 OpenAI 风格 content 数组，
因此多模态分析必须通过外部 OpenAI-compatible vision 网关完成。
"""

from __future__ import annotations

import asyncio
import base64

import httpx

from weekly_report_platform.domain.mail_analysis.llm_client import LLMServiceError, _extract_response_text
from weekly_report_platform.infrastructure.config import VisionSettings, load_project_env
from weekly_report_platform.infrastructure.logging import logger


class MultimodalLLMClient:
    """多模态 LLM 调用封装，支持文本 + 图片输入。

    使用独立的外部 vision 网关（VisionSettings），而非 HundSun 内部 LLM 网关。
    HundSun 内部网关只接受纯字符串 content，不支持 OpenAI 风格 content 数组。
    """

    def __init__(self, *, model: str | None = None) -> None:
        """初始化。model 为空时使用 VisionSettings 中的默认模型。"""
        load_project_env()
        settings = VisionSettings()
        self.url = settings.url
        self.headers = {
            "Authorization": settings.authorization,
            "Content-Type": "application/json",
        }
        self.model = model or settings.model
        self.max_tokens = settings.max_tokens
        self.temperature = settings.temperature
        self.timeout = settings.timeout

    async def acall(
        self,
        *,
        text_prompt: str,
        image_data: bytes,
        image_content_type: str,
    ) -> str:
        """发送一次多模态请求并返回文本结果。

        Args:
            text_prompt: 提示词文本。
            image_data: 图片 bytes。
            image_content_type: MIME 类型，如 "image/png"。
        """
        if not self.url:
            raise LLMServiceError("VISION_URL is not configured")

        base64_encoded = base64.b64encode(image_data).decode("ascii")
        payload = {
            "max_tokens": self.max_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": text_prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{image_content_type};base64,{base64_encoded}",
                                "detail": "high",
                            },
                        },
                    ],
                }
            ],
            "model": self.model,
            "temperature": self.temperature,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
                response = await client.post(self.url, headers=self.headers, json=payload)
                response.raise_for_status()

            return _extract_response_text(response.json())
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else "unknown"
            response_text = exc.response.text[:500].strip() if exc.response is not None else ""
            message = f"HTTP {status_code} calling multimodal LLM API"
            if response_text:
                message = f"{message}: {response_text}"
            logger.error(message)
            raise LLMServiceError(message) from exc
        except httpx.RequestError as exc:
            message = f"Multimodal LLM API network error: {exc}"
            logger.error(message)
            raise LLMServiceError(message) from exc
        except ValueError as exc:
            message = f"Failed to decode multimodal LLM response JSON: {exc}"
            logger.error(message)
            raise LLMServiceError(message) from exc

    async def acall_batch(
        self,
        *,
        text_prompt: str,
        images: list[tuple[bytes, str]],
    ) -> list[str | LLMServiceError]:
        """批量发送多模态请求，每张图片独立调用。

        单张图片调用失败不阻塞其他图片，失败结果以 LLMServiceError 实例返回。

        Args:
            text_prompt: 提示词文本。
            images: list of (image_data, content_type)。

        Returns:
            list[str | LLMServiceError]，成功项为文本结果，失败项为异常对象。
        """
        async def safe_call(image_data: bytes, content_type: str) -> str | LLMServiceError:
            try:
                return await self.acall(
                    text_prompt=text_prompt,
                    image_data=image_data,
                    image_content_type=content_type,
                )
            except LLMServiceError as exc:
                return exc

        coros = [safe_call(img_data, ct) for img_data, ct in images]
        return list(await asyncio.gather(*coros))


multimodal_llm_client = MultimodalLLMClient()