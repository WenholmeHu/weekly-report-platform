"""List DingTalk table ids and field metadata for a base.

This script is read-only. It calls the DingTalk MCP `get_tables` tool and
prints a compact JSON summary that can be used to update table configuration.
"""

# 使用方式：.venv\Scripts\python.exe tools\list_dingtalk_tables.py --base-id ZX6GRezwJl7DbxOxirwj3wGzVdqbropQ

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from weekly_report_platform.dingtalk import run_mcporter  # noqa: E402
from weekly_report_platform.infrastructure.config import (  # noqa: E402
    DEFAULT_DINGTALK_CONFIG_PATH,
    load_dingtalk_targets,
    load_project_env,
)


def _extract_tables(response: dict[str, Any]) -> list[dict[str, Any]]:
    tables = response.get("tables")
    if isinstance(tables, list):
        return [table for table in tables if isinstance(table, dict)]

    data = response.get("data")
    if isinstance(data, dict) and isinstance(data.get("tables"), list):
        return [table for table in data["tables"] if isinstance(table, dict)]

    return []


def _string_value(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return str(value)
    return ""


def _summarize_field(field: dict[str, Any]) -> dict[str, str]:
    return {
        "fieldId": _string_value(field, "fieldId", "id"),
        "fieldName": _string_value(field, "fieldName", "name"),
        "type": _string_value(field, "type"),
    }


def _summarize_table(table: dict[str, Any]) -> dict[str, object]:
    raw_fields = table.get("fields")
    fields = raw_fields if isinstance(raw_fields, list) else []
    return {
        "tableId": _string_value(table, "tableId", "id"),
        "tableName": _string_value(table, "tableName", "name"),
        "fields": [_summarize_field(field) for field in fields if isinstance(field, dict)],
    }


def fetch_tables_with_fields(*, base_id: str, mcp_url: str) -> dict[str, object]:
    """Fetch all tables under `base_id` and return their field metadata."""
    response = run_mcporter(
        mcp_url,
        ["get_tables", "--args", json.dumps({"baseId": base_id}, ensure_ascii=False)],
    )
    tables = [_summarize_table(table) for table in _extract_tables(response)]
    return {
        "baseId": base_id,
        "tableCount": len(tables),
        "tables": tables,
    }


def _default_base_id() -> str:
    targets = load_dingtalk_targets(DEFAULT_DINGTALK_CONFIG_PATH)
    if not targets:
        return ""
    return str(targets[0].get("baseId", ""))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List DingTalk table ids and fields for a baseId.",
    )
    parser.add_argument(
        "--base-id",
        help="DingTalk baseId. Defaults to the first configured target baseId.",
    )
    parser.add_argument(
        "--mcp-url",
        help="DingTalk MCP URL. Defaults to DINGTALK_MCP_URL from .env.",
    )
    return parser.parse_args()


def main() -> int:
    load_project_env()
    args = _parse_args()

    base_id = (args.base_id or _default_base_id()).strip()
    if not base_id:
        raise SystemExit("baseId is required. Pass --base-id or configure a DingTalk target.")

    mcp_url = (args.mcp_url or os.environ.get("DINGTALK_MCP_URL", "")).strip()
    if not mcp_url:
        raise SystemExit("DINGTALK_MCP_URL is required in .env or pass --mcp-url.")

    result = fetch_tables_with_fields(base_id=base_id, mcp_url=mcp_url)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
