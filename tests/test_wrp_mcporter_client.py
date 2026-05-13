"""mcporter 客户端测试。"""

from subprocess import CompletedProcess

import pytest

from weekly_report_platform import dingtalk as mcporter_client


def test_wrp_mcporter_client_decodes_utf8_stdout(monkeypatch) -> None:
    # 成功响应的 stdout 应按 UTF-8 正确解码成 JSON。
    monkeypatch.setattr(mcporter_client, "resolve_mcporter_command", lambda: ["mcporter"])
    monkeypatch.setattr(
        mcporter_client.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(
            args=args[0],
            returncode=0,
            stdout='{"summary":"项目周报"}'.encode("utf-8"),
            stderr=b"",
        ),
    )

    result = mcporter_client.run_mcporter("https://example.com/mcp", ["get_tables"])

    assert result["summary"] == "项目周报"


def test_wrp_mcporter_client_uses_decoded_stderr_for_failures(monkeypatch) -> None:
    # 失败时应优先抛出已解码的 stderr，便于定位真实下游报错。
    monkeypatch.setattr(mcporter_client, "resolve_mcporter_command", lambda: ["mcporter"])
    monkeypatch.setattr(
        mcporter_client.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(
            args=args[0],
            returncode=1,
            stdout=b"",
            stderr="同步失败".encode("utf-8"),
        ),
    )

    with pytest.raises(RuntimeError, match="同步失败"):
        mcporter_client.run_mcporter("https://example.com/mcp", ["create_records"])


def test_wrp_mcporter_client_rejects_invalid_json_stdout(monkeypatch) -> None:
    monkeypatch.setattr(mcporter_client, "resolve_mcporter_command", lambda: ["mcporter"])
    monkeypatch.setattr(
        mcporter_client.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=b"not-json",
            stderr=b"",
        ),
    )

    with pytest.raises(RuntimeError, match="invalid JSON"):
        mcporter_client.run_mcporter("https://example.com/mcp", ["get_tables"])
