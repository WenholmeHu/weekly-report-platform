"""提示词结果解析测试。"""

import asyncio

from weekly_report_platform.domain.mail_analysis import prompts


def test_wrp_extract_progress_from_report_rejects_non_object_response(monkeypatch) -> None:
    async def fake_acall(_: str) -> str:
        return '[{"bad": true}]'

    monkeypatch.setattr(prompts.llm_client, "acall", fake_acall)

    result, error = asyncio.run(prompts.extract_progress_from_report("demo"))

    assert result is None
    assert error is not None
    assert "JSON object" in error


def test_wrp_extract_hidden_risk_from_report_ignores_invalid_raw_extract_shape(monkeypatch) -> None:
    async def fake_acall(_: str) -> str:
        return '[{"序号": 1, "风险描述": "隐患"}]'

    monkeypatch.setattr(prompts.llm_client, "acall", fake_acall)

    result, error = asyncio.run(
        prompts.extract_hidden_risk_from_report(
            "demo",
            {prompts.RAW_EXTRACT_KEY: "not-a-list"},
        )
    )

    assert error is None
    assert result == [{"序号": 1, "风险描述": "隐患"}]


# ---- 提示词图片引导语测试 ----

def test_wrp_progress_extract_prompt_contains_image_guidance() -> None:
    """进度抽取提示词应包含图片相关引导语。"""
    prompt = prompts.get_progress_extract_prompt("formatted text")
    assert "Image Content" in prompt
    assert "vision model" in prompt.lower() or "image description" in prompt.lower()


def test_wrp_risk_extract_prompt_contains_image_guidance() -> None:
    """显式风险抽取提示词应包含图片相关引导语。"""
    prompt = prompts.get_risk_extract_prompt("formatted text")
    assert "Image Content" in prompt


def test_wrp_hidden_risk_extract_prompt_contains_image_guidance() -> None:
    """隐藏风险抽取提示词应包含图片相关引导语。"""
    prompt = prompts.get_hidden_risk_extract_prompt("(no known risks)", "formatted text")
    assert "image" in prompt.lower()

