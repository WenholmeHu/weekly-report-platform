"""LLM 客户端测试。"""

import pytest

from weekly_report_platform.domain.mail_analysis import llm_client


def test_wrp_llm_client_extract_response_text_supports_text_blocks() -> None:
    result = llm_client._extract_response_text(
        {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": '{"ok": '},
                            {"type": "text", "text": '"yes"}'},
                        ]
                    }
                }
            ]
        }
    )

    assert result == '{"ok": "yes"}'


def test_wrp_llm_client_extract_response_text_rejects_invalid_payload() -> None:
    with pytest.raises(llm_client.LLMServiceError, match="choices"):
        llm_client._extract_response_text({"data": []})
