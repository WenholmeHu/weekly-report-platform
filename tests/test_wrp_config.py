"""配置加载测试。"""

import json

import pytest

from weekly_report_platform.infrastructure import config as config_module


def test_wrp_default_config_file_contains_targets() -> None:
    data = json.loads(config_module.DEFAULT_DINGTALK_CONFIG_PATH.read_text(encoding="utf-8"))
    assert "targets" in data
    assert isinstance(data["targets"], list)
    assert data["targets"]


def test_wrp_load_dingtalk_targets_reads_project_env(monkeypatch, workspace_tmp_path) -> None:
    (workspace_tmp_path / ".env").write_text(
        "DINGTALK_MCP_URL=https://example.com/mcp\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DINGTALK_MCP_URL", raising=False)
    monkeypatch.delenv("DINGTALK_CONFIG_PATH", raising=False)
    monkeypatch.delenv("DINGTALK_CONFIG_JSON", raising=False)
    monkeypatch.setattr(config_module, "PROJECT_ROOT", workspace_tmp_path, raising=False)

    targets = config_module.load_dingtalk_targets(config_module.DEFAULT_DINGTALK_CONFIG_PATH)

    assert targets[0]["mcp_url"] == "https://example.com/mcp"
    assert targets[0]["fields"]["message_id"]["sourcePath"] == "message_id"
    assert targets[0]["fields"]["project_progress"]["sourcePath"] == "analysis.进度抽取结果.项目进度"


def test_wrp_load_dingtalk_targets_requires_mcp_url(monkeypatch, workspace_tmp_path) -> None:
    monkeypatch.delenv("DINGTALK_MCP_URL", raising=False)
    monkeypatch.delenv("DINGTALK_CONFIG_PATH", raising=False)
    monkeypatch.delenv("DINGTALK_CONFIG_JSON", raising=False)
    monkeypatch.setattr(config_module, "PROJECT_ROOT", workspace_tmp_path, raising=False)

    with pytest.raises(ValueError, match="DINGTALK_MCP_URL is required"):
        config_module.load_dingtalk_targets(config_module.DEFAULT_DINGTALK_CONFIG_PATH)


def test_wrp_load_dingtalk_targets_supports_multiple_tables(monkeypatch) -> None:
    monkeypatch.setenv("DINGTALK_MCP_URL", "https://example.com/mcp")
    monkeypatch.setenv(
        "DINGTALK_CONFIG_JSON",
        json.dumps(
            {
                "fields": {
                    "message_id": {
                        "fieldName": "邮件标识",
                        "type": "text",
                        "required": False,
                        "sourcePath": "message_id",
                    },
                    "subject": {
                        "fieldName": "项目周报",
                        "type": "text",
                        "required": True,
                        "sourcePath": "subject",
                    },
                },
                "targets": [
                    {"baseId": "base-1", "tableId": "table-1"},
                    {"baseId": "base-1", "tableId": "table-2"},
                ],
            },
            ensure_ascii=False,
        ),
    )
    monkeypatch.delenv("DINGTALK_CONFIG_PATH", raising=False)

    targets = config_module.load_dingtalk_targets(config_module.DEFAULT_DINGTALK_CONFIG_PATH)

    assert [target["tableId"] for target in targets] == ["table-1", "table-2"]
    assert all(target["mcp_url"] == "https://example.com/mcp" for target in targets)


def test_wrp_load_dingtalk_targets_raises_clear_error_for_missing_target_keys(monkeypatch) -> None:
    monkeypatch.setenv("DINGTALK_MCP_URL", "https://example.com/mcp")
    monkeypatch.setenv(
        "DINGTALK_CONFIG_JSON",
        json.dumps(
            {
                "targets": [{"baseId": "base-1"}],
            },
            ensure_ascii=False,
        ),
    )
    monkeypatch.delenv("DINGTALK_CONFIG_PATH", raising=False)

    with pytest.raises(ValueError, match="must contain both 'baseId' and 'tableId'"):
        config_module.load_dingtalk_targets(config_module.DEFAULT_DINGTALK_CONFIG_PATH)


def test_wrp_load_dingtalk_targets_raises_clear_error_for_invalid_fields(monkeypatch) -> None:
    monkeypatch.setenv("DINGTALK_MCP_URL", "https://example.com/mcp")
    monkeypatch.setenv(
        "DINGTALK_CONFIG_JSON",
        json.dumps(
            {
                "fields": [],
                "targets": [{"baseId": "base-1", "tableId": "table-1"}],
            },
            ensure_ascii=False,
        ),
    )
    monkeypatch.delenv("DINGTALK_CONFIG_PATH", raising=False)

    with pytest.raises(ValueError, match="DingTalk config 'fields' must be a JSON object"):
        config_module.load_dingtalk_targets(config_module.DEFAULT_DINGTALK_CONFIG_PATH)
