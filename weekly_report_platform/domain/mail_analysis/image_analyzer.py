"""图片分析编排模块。

接收有效图片列表，调用多模态 LLM 进行分析，生成结构化描述文本。
策略 B：图片分析结果转化为描述文本，后续注入 prompts.py 的提示词中。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from weekly_report_platform.domain.mail_analysis.image_filter import ValidImage
from weekly_report_platform.domain.mail_analysis.llm_client import LLMServiceError
from weekly_report_platform.domain.mail_analysis.multimodal_llm import multimodal_llm_client
from weekly_report_platform.infrastructure.logging import logger

IMAGE_ANALYSIS_PROMPT = """\
You are analyzing an image embedded in a weekly report email.
Describe the content in a structured, concise manner.
Focus on: progress indicators, charts, tables, risks, and any actionable information.
If the image is a chart/screenshot/table, extract key data points and conclusions.
If the image is decorative or irrelevant, respond with "No meaningful content".
"""


@dataclass(frozen=True)
class ImageAnalysisResult:
    """多模态 LLM 对单张图片的分析结果。"""

    filename: str  # 对应图片文件名
    description: str  # LLM 描述文本
    error: str = ""  # 分析失败时的错误信息


async def analyze_image(
    image_data: bytes,
    content_type: str,
    filename: str,
) -> ImageAnalysisResult:
    """分析单张图片。

    调用多模态 LLM，失败时在 error 字段记录错误信息，不抛出异常。
    """
    try:
        result = await multimodal_llm_client.acall(
            text_prompt=IMAGE_ANALYSIS_PROMPT,
            image_data=image_data,
            image_content_type=content_type,
        )
        description = result.strip()
        if description == "No meaningful content":
            description = ""
        return ImageAnalysisResult(filename=filename, description=description)
    except LLMServiceError as exc:
        logger.warning(f"图片分析失败 [{filename}]: {exc}")
        return ImageAnalysisResult(filename=filename, description="", error=str(exc))
    except Exception as exc:
        logger.warning(f"图片分析异常 [{filename}]: {exc}")
        return ImageAnalysisResult(filename=filename, description="", error=str(exc))


async def analyze_images(images: list[ValidImage]) -> list[ImageAnalysisResult]:
    """批量分析有效图片。

    空列表直接返回空列表。单张图片失败不影响其他图片的分析。
    """
    if not images:
        return []

    coros = [
        analyze_image(img.data, img.content_type, img.filename)
        for img in images
    ]
    return list(await asyncio.gather(*coros))