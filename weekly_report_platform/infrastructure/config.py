from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
RESOURCES_ROOT = PACKAGE_ROOT / "resources"
DEFAULT_DINGTALK_CONFIG_PATH = RESOURCES_ROOT / "config.local.json"
DEFAULT_PROCESSED_STATE_PATH = PROJECT_ROOT / ".state" / "processed_message_ids.json"
DEFAULT_AUTOMATION_STATE_PATH = PROJECT_ROOT / ".state" / "automation_config.json"


def load_project_env() -> None:
    """加载项目根目录下的 `.env`。"""
    load_dotenv(PROJECT_ROOT / ".env")


@dataclass
class MailSettings:
    """邮箱连接配置。"""

    pop3_host: str = field(default_factory=lambda: os.getenv("EMAIL_POP3_HOST", "pop3.hundsun.cn"))
    pop3_port: int = field(default_factory=lambda: int(os.getenv("EMAIL_POP3_PORT", "995")))
    username: str = field(default_factory=lambda: os.getenv("EMAIL_USERNAME", ""))
    password: str = field(default_factory=lambda: os.getenv("EMAIL_PASSWORD", ""))
    subject_keyword: str = field(default_factory=lambda: os.getenv("EMAIL_SUBJECT_KEYWORD", "周报"))


@dataclass
class LLMSettings:
    """LLM 请求配置。"""

    url: str = field(default_factory=lambda: os.getenv("LLM_URL", ""))
    model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", ""))
    authorization: str = field(default_factory=lambda: os.getenv("LLM_AUTHORIZATION", ""))
    max_tokens: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_TOKENS", "10000")))
    temperature: float = field(default_factory=lambda: float(os.getenv("LLM_TEMPERATURE", "0.01")))
    stream: bool = field(default_factory=lambda: os.getenv("LLM_STREAM", "false").lower() == "true")
    n: int = field(default_factory=lambda: int(os.getenv("LLM_N", "1")))


def build_default_fields() -> dict[str, dict[str, object]]:
    """构造默认的钉钉字段映射。"""
    return {
        "message_id": {
            "fieldName": "邮件标识",
            "type": "text",
            "required": False,
            "sourcePath": "message_id",
        },
        "project_name": {
            "fieldName": "项目名称",
            "type": "text",
            "required": True,
            "sourcePath": "analysis.进度抽取结果.项目名称",
        },
        "project_code": {
            "fieldName": "项目编号",
            "type": "text",
            "required": True,
            "sourcePath": "analysis.进度抽取结果.项目编号",
        },
        "project_cycle": {
            "fieldName": "项目周报周期",
            "type": "text",
            "required": True,
            "sourcePath": "analysis.进度抽取结果.项目周报周期",
        },
        "project_progress": {
            "fieldName": "项目进度",
            "type": "progress",
            "required": True,
            "sourcePath": "analysis.进度抽取结果.项目进度",
        },
        "milestone": {
            "fieldName": "里程碑",
            "type": "text",
            "required": True,
            "sourcePath": "analysis.进度抽取结果.里程碑",
        },
        "risk_details": {
            "fieldName": "风险详情",
            "type": "text",
            "required": False,
            "sourcePath": "analysis.风险抽取结果.风险详情",
        },
        "overall_progress": {
            "fieldName": "项目整体进展",
            "type": "text",
            "required": False,
            "sourcePath": "analysis.进度抽取结果.综合总结",
        },
        "weekly_summary": {
            "fieldName": "本周小结",
            "type": "text",
            "required": False,
            "sourcePath": "analysis.进度抽取结果.本周小结",
        },
        "next_week_plan": {
            "fieldName": "下周计划",
            "type": "text",
            "required": False,
            "sourcePath": "analysis.进度抽取结果.下周计划",
        },
        "extract_date": {
            "fieldName": "邮箱抽取日期",
            "type": "date",
            "required": False,
            "sourcePath": "processed_at",
        },
    }


def _require_object(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _require_object(payload, name=f"config file '{path}'")


def load_dingtalk_config_data(config_path: Path) -> dict[str, object]:
    """读取钉钉配置。

    优先级：
    1. `DINGTALK_CONFIG_JSON`
    2. `DINGTALK_CONFIG_PATH`
    3. 代码默认配置文件
    """
    config_json = os.environ.get("DINGTALK_CONFIG_JSON", "").strip()
    if config_json:
        return _require_object(json.loads(config_json), name="DINGTALK_CONFIG_JSON")

    config_path_text = os.environ.get("DINGTALK_CONFIG_PATH", "").strip()
    if config_path_text:
        resolved_path = Path(config_path_text)
        if not resolved_path.is_absolute():
            resolved_path = PROJECT_ROOT / resolved_path
        return _read_json_object(resolved_path)

    return _read_json_object(config_path)


def _build_target(
    *,
    raw_target: dict[str, object],
    fields: dict[str, dict[str, object]],
    mcp_url: str,
) -> dict[str, object]:
    if "baseId" not in raw_target or "tableId" not in raw_target:
        raise ValueError("each DingTalk target must contain both 'baseId' and 'tableId'")
    return {
        "baseId": raw_target["baseId"],
        "tableId": raw_target["tableId"],
        "fields": raw_target.get("fields", fields),
        "mcp_url": mcp_url,
    }


def load_dingtalk_targets(config_path: Path) -> list[dict[str, object]]:
    """读取所有钉钉目标表配置。"""
    load_project_env()
    data = load_dingtalk_config_data(config_path)

    mcp_url = os.environ.get("DINGTALK_MCP_URL", "").strip()
    if not mcp_url:
        raise ValueError("DINGTALK_MCP_URL is required")

    fields = data.get("fields", build_default_fields())
    if not isinstance(fields, dict):
        raise ValueError("DingTalk config 'fields' must be a JSON object")

    raw_targets = data.get("targets")
    if isinstance(raw_targets, list) and raw_targets:
        return [
            _build_target(
                raw_target=_require_object(raw_target, name="DingTalk target"),
                fields=fields,
                mcp_url=mcp_url,
            )
            for raw_target in raw_targets
        ]

    return [
        _build_target(
            raw_target=_require_object(data, name="DingTalk config"),
            fields=fields,
            mcp_url=mcp_url,
        )
    ]
