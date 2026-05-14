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


