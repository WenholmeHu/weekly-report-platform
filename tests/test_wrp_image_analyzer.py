"""图片分析编排测试。

monkeypatch MultimodalLLMClient.acall，不访问真实 LLM 服务。
"""

import asyncio

import pytest

from weekly_report_platform.domain.mail_analysis.image_analyzer import (
    IMAGE_ANALYSIS_PROMPT,
    ImageAnalysisResult,
    analyze_image,
    analyze_images,
)
from weekly_report_platform.domain.mail_analysis.image_filter import ValidImage
from weekly_report_platform.domain.mail_analysis.llm_client import LLMServiceError
from weekly_report_platform.domain.mail_analysis.multimodal_llm import MultimodalLLMClient


async def _fake_acall_ok(self, *, text_prompt: str, image_data: bytes, image_content_type: str) -> str:
    """模拟 acall 正常返回。"""
    return f"图片描述: {image_content_type}"


async def _fake_acall_fail(self, *, text_prompt: str, image_data: bytes, image_content_type: str) -> str:
    """模拟 acall 抛出异常。"""
    raise LLMServiceError("模拟 LLM 调用失败")


# ---- 空图片列表 ----

def test_analyze_images_empty_list_returns_empty() -> None:
    """空图片列表应返回空列表，不调用 LLM。"""
    # 不调用 monkeypatch，因为不应该走到 acall
    results = asyncio.run(analyze_images([]))
    assert results == []


# ---- 单张正常分析 ----

def test_analyze_image_normal(monkeypatch) -> None:
    """单张图片正常分析应返回有效描述。"""
    monkeypatch.setattr(MultimodalLLMClient, "acall", _fake_acall_ok)
    result = asyncio.run(
        analyze_image(b"img_data", "image/png", "chart001.png")
    )
    assert result.filename == "chart001.png"
    assert result.description == "图片描述: image/png"
    assert result.error == ""


# ---- 单张正常分析验证 prompt ----

def test_analyze_image_sends_correct_prompt(monkeypatch) -> None:
    """传递给 acall 的 text_prompt 应使用 IMAGE_ANALYSIS_PROMPT。"""
    captured_prompt = None

    async def capturing_acall(self, *, text_prompt, image_data, image_content_type):
        nonlocal captured_prompt
        captured_prompt = text_prompt
        return "ok"

    monkeypatch.setattr(MultimodalLLMClient, "acall", capturing_acall)
    asyncio.run(analyze_image(b"img", "image/png", "test.png"))
    assert captured_prompt == IMAGE_ANALYSIS_PROMPT


# ---- No meaningful content ----

def test_analyze_image_no_meaningful_content(monkeypatch) -> None:
    """LLM 返回 "No meaningful content" 时 description 应为空。"""
    async def fake_acall(self, *, text_prompt, image_data, image_content_type):
        return "No meaningful content"

    monkeypatch.setattr(MultimodalLLMClient, "acall", fake_acall)
    result = asyncio.run(
        analyze_image(b"img", "image/png", "deco.png")
    )
    assert result.description == ""
    assert result.error == ""
    assert result.filename == "deco.png"


# ---- 分析失败 ----

def test_analyze_image_error(monkeypatch) -> None:
    """分析失败时 error 字段应包含错误信息。"""
    monkeypatch.setattr(MultimodalLLMClient, "acall", _fake_acall_fail)
    result = asyncio.run(
        analyze_image(b"img", "image/png", "broken.png")
    )
    assert result.description == ""
    assert result.error != ""
    assert "模拟 LLM 调用失败" in result.error
    assert result.filename == "broken.png"


# ---- 批量分析（全成功） ----

def test_analyze_images_all_success(monkeypatch) -> None:
    """批量分析多张图片应全部成功。"""
    monkeypatch.setattr(MultimodalLLMClient, "acall", _fake_acall_ok)
    images = [
        ValidImage(data=b"img1", content_type="image/png", filename="chart.png", size=1000),
        ValidImage(data=b"img2", content_type="image/jpeg", filename="table.jpg", size=2000),
        ValidImage(data=b"img3", content_type="image/gif", filename="graph.gif", size=1500),
    ]
    results = asyncio.run(analyze_images(images))
    assert len(results) == 3
    assert all(isinstance(r, ImageAnalysisResult) for r in results)
    assert {r.filename for r in results} == {"chart.png", "table.jpg", "graph.gif"}
    assert all(r.description == "图片描述: image/png" or r.description == "图片描述: image/jpeg" or r.description == "图片描述: image/gif" for r in results)
    assert all(r.error == "" for r in results)


# ---- 批量分析（混合成功/失败） ----

def test_analyze_images_mixed_success_failure(monkeypatch) -> None:
    """批量分析中部分图片失败不应影响其他图片的结果。"""
    call_count = 0

    async def mixed_acall(self, *, text_prompt, image_data, image_content_type):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise LLMServiceError("模拟第二张图片分析失败")
        return f"图片{call_count}的描述"

    monkeypatch.setattr(MultimodalLLMClient, "acall", mixed_acall)
    images = [
        ValidImage(data=b"img1", content_type="image/png", filename="chart.png", size=1000),
        ValidImage(data=b"img2", content_type="image/jpeg", filename="error.jpg", size=2000),
        ValidImage(data=b"img3", content_type="image/gif", filename="graph.gif", size=1500),
    ]
    results = asyncio.run(analyze_images(images))
    assert len(results) == 3
    assert results[0].description == "图片1的描述"
    assert results[0].error == ""
    assert results[1].description == ""
    assert "模拟第二张图片分析失败" in results[1].error
    assert results[2].description == "图片3的描述"
    assert results[2].error == ""


# ---- ImageAnalysisResult 是不可变 dataclass ----

def test_image_analysis_result_is_frozen() -> None:
    """ImageAnalysisResult 应为不可变 dataclass。"""
    from dataclasses import FrozenInstanceError
    result = ImageAnalysisResult(filename="test.png", description="ok")
    with pytest.raises(FrozenInstanceError):
        result.description = "changed"


# ---- No meaningful content 变体 ----

def test_analyze_image_whitespace_meaningless(monkeypatch) -> None:
    """返回内容包含前后空白时，description 应正确 trim。"""
    async def fake_acall_with_whitespace(self, *, text_prompt, image_data, image_content_type):
        return "  这是一段有意义的描述   "

    monkeypatch.setattr(MultimodalLLMClient, "acall", fake_acall_with_whitespace)
    result = asyncio.run(
        analyze_image(b"img", "image/png", "test.png")
    )
    assert result.description == "这是一段有意义的描述"
