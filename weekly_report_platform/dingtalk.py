"""钉钉同步逻辑。

最重要的入口只有两个：
- `sync_analysis_record()`：同步一封邮件的分析结果。
- `sync_analysis_records()`：同步一批分析结果。

写入钉钉真正发生在 `_write_records_to_sync_target()` 中。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from weekly_report_platform.infrastructure.config import load_dingtalk_targets


PROGRESS_PATH = "analysis.进度抽取结果.分析结果"
RISK_DETAIL_PATH = "analysis.风险抽取结果.风险详情"
HIDDEN_RISK_PATH = "analysis.隐藏风险"

SUMMARY_KEY = "综合总结"
CONSISTENCY_KEY = "一致性分析"
SEQ_KEY = "序号"
RISK_DESC_KEY = "风险描述"
RISK_TYPE_KEY = "风险类型"
RISK_ACTION_KEY = "风险措施和最新进展"
RISK_OWNER_KEY = "风险责任人"
RISK_SUMMARY_KEY = "风险归纳"


@dataclass(frozen=True)
class SyncSourceRecord:
    """待同步的数据源。

    payload:
    分析后的 JSON 对象，通常就是一封邮件最终落盘前或回读后的内容。

    source_path:
    对应 artifact 文件路径。主要用于 manifest 记录和排查问题。

    load_error:
    如果上游 artifact 回读失败，这里会带着错误信息进入同步层。
    """

    payload: dict[str, Any] | None
    source_path: Path | None = None
    load_error: str = ""


@dataclass(frozen=True)
class SyncItemResult:
    """一封邮件的最终同步结果。"""

    status: str
    message_id: str
    subject: str
    source_path: Path | None
    processed_at: str
    error: str
    synced_at: str | None


@dataclass(frozen=True)
class SyncBatchResult:
    """一批邮件的汇总同步结果。"""

    total: int
    synced: int
    skipped: int
    failed: int
    items: list[SyncItemResult]


@dataclass(frozen=True)
class SyncTarget:
    """一张钉钉目标表。"""

    base_id: str
    table_id: str
    fields: dict[str, dict[str, object]]
    mcp_url: str


def resolve_mcporter_command() -> list[str]:
    """解析 mcporter 命令。

    优先直接使用系统里可执行的 `mcporter`。
    如果装的是 npm 全局 wrapper，则尽量解析到真实的 `node cli.js`。
    最后退化到 `npx.cmd mcporter`。
    """
    mcporter_path = shutil.which("mcporter")
    if mcporter_path:
        wrapper_path = Path(mcporter_path)
        if wrapper_path.suffix.lower() in {".cmd", ".ps1"}:
            node_path = shutil.which("node")
            cli_path = wrapper_path.parent / "node_modules" / "mcporter" / "dist" / "cli.js"
            if node_path and cli_path.exists():
                return [node_path, str(cli_path)]
        return [mcporter_path]

    npx_path = shutil.which("npx.cmd")
    if npx_path:
        return [npx_path, "mcporter"]

    raise RuntimeError("mcporter is not installed or available via npx.cmd")


def _decode_process_output(raw_output: bytes | None) -> str:
    if raw_output is None:
        return ""
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
        try:
            return raw_output.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_output.decode("utf-8", errors="replace")


def run_mcporter(mcp_url: str, args: list[str]) -> dict[str, Any]:
    """调用 mcporter，并要求返回 JSON object。"""
    if not args:
        raise ValueError("mcporter args must not be empty")

    command = [
        *resolve_mcporter_command(),
        "call",
        "--http-url",
        mcp_url,
        "--name",
        "adhoc-http",
        "--tool",
        args[0],
        *args[1:],
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=False, timeout=60)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("mcporter call timed out") from exc

    stdout_text = _decode_process_output(result.stdout)
    stderr_text = _decode_process_output(result.stderr)
    if result.returncode != 0:
        raise RuntimeError(stderr_text.strip() or "mcporter call failed")

    try:
        payload = json.loads(stdout_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"mcporter returned invalid JSON: {stdout_text[:500].strip()}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("mcporter response must be a JSON object")
    return payload


def build_field_map(table_response: dict[str, Any], field_specs: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
    """把配置中的字段别名映射成钉钉真实 fieldId。"""
    data = table_response.get("data")
    tables = table_response.get("tables") or (data.get("tables") if isinstance(data, dict) else []) or []
    fields = tables[0].get("fields", []) if tables else []
    mapping: dict[str, dict[str, object]] = {}

    for field_key, spec in field_specs.items():
        expected_name = spec["fieldName"]
        for field in fields:
            if field["fieldName"] == expected_name:
                mapping[field_key] = {**spec, "fieldId": field["fieldId"], "fieldName": expected_name}
                break

    required_keys = [field_key for field_key, spec in field_specs.items() if spec.get("required", False)]
    if any(field_key not in mapping for field_key in required_keys):
        raise ValueError("required fields not found")
    return mapping


def resolve_source_value(item: dict[str, object], field_key: str, field_meta: dict[str, object]) -> object:
    """根据 sourcePath 从分析结果中取值。"""
    source_path = field_meta.get("sourcePath")
    if not source_path:
        return item.get(field_key)

    value: object = item
    for segment in str(source_path).split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(segment)
        if value is None:
            return None
    return value


def stringify_text_part(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def format_progress_analysis_text(value: object) -> str:
    """把进度分析对象格式化成适合写入单个文本单元格的字符串。"""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return stringify_text_part(value)

    lines: list[str] = []
    for key in (SUMMARY_KEY, CONSISTENCY_KEY):
        if key in value and value[key] is not None:
            lines.append(f"{key}: {stringify_text_part(value[key])}")
    for key, item_value in value.items():
        if key not in {SUMMARY_KEY, CONSISTENCY_KEY} and item_value is not None:
            lines.append(f"{key}: {stringify_text_part(item_value)}")
    return "\n".join(lines)


def format_risk_section(item: dict[str, object], index: int, detail_fields: tuple[str, ...]) -> str:
    """把一条风险对象拼成一段文本。"""
    number = item.get(SEQ_KEY, index + 1)
    lines = [f"{number}. {RISK_DESC_KEY}: {stringify_text_part(item.get(RISK_DESC_KEY, ''))}"]
    for key in detail_fields:
        if item.get(key) is not None:
            lines.append(f"{key}: {stringify_text_part(item[key])}")
    return "\n".join(lines)


def format_risk_array_text(value: object, detail_fields: tuple[str, ...]) -> str:
    """把风险数组格式化成多段文本。"""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return stringify_text_part(value)

    sections: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            return stringify_text_part(value)
        sections.append(format_risk_section(item, index, detail_fields))
    return "\n\n".join(sections)


def normalize_text_value(field_meta: dict[str, object], value: object) -> str:
    """按业务字段类型把复杂对象转成钉钉文本字段。"""
    source_path = str(field_meta.get("sourcePath", ""))
    if source_path == PROGRESS_PATH:
        return format_progress_analysis_text(value)
    if source_path == RISK_DETAIL_PATH:
        return format_risk_array_text(value, (RISK_TYPE_KEY, RISK_ACTION_KEY, RISK_OWNER_KEY))
    if source_path == HIDDEN_RISK_PATH:
        return format_risk_array_text(value, (RISK_TYPE_KEY, RISK_SUMMARY_KEY, RISK_ACTION_KEY))
    return stringify_text_part(value)


def normalize_cell_value(field_name: str, field_type: str, value: object, field_meta: dict[str, object]) -> object:
    """把业务值转换成钉钉建表接口接受的单元格值。"""
    if field_type == "text":
        return normalize_text_value(field_meta, value)
    if field_type == "number":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        if isinstance(value, str):
            return int(value) if value.isdigit() else float(value)
        raise ValueError(f"invalid number value for field: {field_name}")
    if field_type == "date":
        if not isinstance(value, str):
            raise ValueError(f"invalid date value for field: {field_name}")
        return value
    raise ValueError(f"unsupported field type: {field_type}")


def build_records_payload(
    base_id: str,
    table_id: str,
    records: list[dict[str, Any]],
    field_map: dict[str, dict[str, object]],
) -> dict[str, object]:
    """构造 `create_records` 的请求体。"""
    payload_records = []
    for item in records:
        cells: dict[str, object] = {}
        for field_key, field_meta in field_map.items():
            field_name = str(field_meta["fieldName"])
            field_type = str(field_meta["type"])
            required = bool(field_meta.get("required", False))
            value = resolve_source_value(item, field_key, field_meta)
            if value is None:
                if required:
                    raise ValueError(f"missing required field: {field_name}")
                continue
            cells[str(field_meta["fieldId"])] = normalize_cell_value(field_name, field_type, value, field_meta)
        payload_records.append({"cells": cells})
    return {"baseId": base_id, "tableId": table_id, "records": payload_records}


def _load_target_field_map(sync_target: SyncTarget) -> dict[str, dict[str, object]]:
    """读取一张目标表的字段定义，并转成 field map。"""
    table_response = run_mcporter(
        sync_target.mcp_url,
        [
            "get_tables",
            "--args",
            json.dumps({"baseId": sync_target.base_id, "tableIds": [sync_target.table_id]}, ensure_ascii=False),
        ],
    )
    return build_field_map(table_response, sync_target.fields)


def _extract_query_records(response: dict[str, Any]) -> list[dict[str, Any]]:
    """兼容不同包裹层，统一提取 query_records 返回的 records。"""
    if isinstance(response.get("records"), list):
        return [record for record in response["records"] if isinstance(record, dict)]
    data = response.get("data")
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return [record for record in data["records"] if isinstance(record, dict)]
    return []


def _query_existing_record_by_message_id(
    *,
    sync_target: SyncTarget,
    message_id: str,
    field_map: dict[str, dict[str, object]],
) -> bool:
    """检查目标表中是否已经存在相同 message_id。"""
    message_id = message_id.strip()
    if not message_id:
        return False

    message_id_field = field_map.get("message_id")
    if not isinstance(message_id_field, dict):
        return False

    message_id_field_id = str(message_id_field["fieldId"])
    response = run_mcporter(
        sync_target.mcp_url,
        [
            "query_records",
            "--args",
            json.dumps(
                {
                    "baseId": sync_target.base_id,
                    "tableId": sync_target.table_id,
                    "fieldIds": [message_id_field_id],
                    "limit": 1,
                    "filters": {
                        "operator": "and",
                        "operands": [{"operator": "eq", "operands": [message_id_field_id, message_id]}],
                    },
                },
                ensure_ascii=False,
            ),
        ],
    )
    for record in _extract_query_records(response):
        cells = record.get("cells")
        if isinstance(cells, dict) and str(cells.get(message_id_field_id, "")).strip() == message_id:
            return True
    return False


def _write_records_to_sync_target(
    *,
    sync_target: SyncTarget,
    records: list[dict[str, Any]],
    field_map: dict[str, dict[str, object]] | None = None,
) -> int:
    """真正调用钉钉写表。"""
    effective_field_map = field_map or _load_target_field_map(sync_target)
    payload = build_records_payload(sync_target.base_id, sync_target.table_id, records, effective_field_map)
    run_mcporter(sync_target.mcp_url, ["create_records", "--args", json.dumps(payload, ensure_ascii=False)])
    return len(records)


def _field_map_cache_key(sync_target: SyncTarget) -> tuple[str, str]:
    """字段映射缓存 key。

    不能只用 `table_id`，否则不同 base 下相同 table_id 会串缓存。
    """
    return sync_target.base_id, sync_target.table_id


def load_sync_manifest(manifest_path: Path) -> dict[str, object]:
    """读取同步清单文件，不存在则返回空清单。"""
    if not manifest_path.exists():
        return {"version": 1, "items": {}}
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("sync manifest must be a JSON object")
    return payload


def save_sync_manifest(manifest_path: Path, manifest: dict[str, object]) -> None:
    """保存同步清单文件。"""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_sync_targets(*, config_path: Path) -> list[SyncTarget]:
    """把配置文件中的原始 target 转成同步层对象。"""
    return [
        SyncTarget(
            base_id=str(target["baseId"]),
            table_id=str(target["tableId"]),
            fields=dict(target["fields"]),
            mcp_url=str(target["mcp_url"]),
        )
        for target in load_dingtalk_targets(config_path)
    ]


def _sync_key(sync_target: SyncTarget, message_id: str) -> str:
    """用于 manifest 中唯一标识“某封邮件写入某张表”的 key。"""
    return f"base::{sync_target.base_id}::table::{sync_target.table_id}::message::{message_id}"


def _manifest_key(
    *,
    sync_target: SyncTarget,
    message_id: str,
    source_path: Path | None,
    subject: str,
    processed_at: str,
    index: int,
) -> str:
    """为无 message_id 的情况生成兜底 manifest key。"""
    if message_id:
        return _sync_key(sync_target, message_id)
    if source_path is not None:
        return f"base::{sync_target.base_id}::table::{sync_target.table_id}::source::{source_path}"
    return (
        f"base::{sync_target.base_id}"
        f"::table::{sync_target.table_id}"
        f"::missing_message_id::{subject or 'unknown_subject'}::{processed_at or 'unknown_processed_at'}::{index}"
    )


def _manifest_entry(result: SyncItemResult, sync_target: SyncTarget) -> dict[str, object]:
    """把同步结果转成可落盘的 manifest 记录。"""
    entry: dict[str, object] = {
        "status": result.status,
        "base_id": sync_target.base_id,
        "table_id": sync_target.table_id,
        "processed_at": result.processed_at,
        "error": result.error,
    }
    if result.message_id:
        entry["message_id"] = result.message_id
    if result.source_path is not None:
        entry["source_file"] = str(result.source_path)
    if result.synced_at is not None:
        entry["synced_at"] = result.synced_at
    return entry


def _sync_source_record(
    source_record: SyncSourceRecord,
    *,
    sync_target: SyncTarget,
    field_map_cache: dict[tuple[str, str], dict[str, dict[str, object]]],
    manifest_items: dict[str, object],
    index: int,
) -> SyncItemResult:
    """同步“单封邮件 -> 单张表”。

    处理顺序：
    1. 输入本身是否损坏。
    2. 是否缺少 message_id。
    3. 本地 manifest 是否已经记为成功。
    4. 分析结果本身是否带 errors。
    5. 目标表中是否已经存在该 message_id。
    6. 真正写入钉钉。
    """
    if source_record.payload is None:
        key = _manifest_key(
            sync_target=sync_target,
            message_id="",
            source_path=source_record.source_path,
            subject="",
            processed_at="",
            index=index,
        )
        result = SyncItemResult(
            status="failed",
            message_id="",
            subject="",
            source_path=source_record.source_path,
            processed_at="",
            error=source_record.load_error,
            synced_at=None,
        )
        manifest_items[key] = _manifest_entry(result, sync_target)
        return result

    payload = source_record.payload
    message_id = str(payload.get("message_id", "")).strip()
    subject = str(payload.get("subject", ""))
    processed_at = str(payload.get("processed_at", "")).strip()
    error_text = str(payload.get("errors", "")).strip()

    key = _manifest_key(
        sync_target=sync_target,
        message_id=message_id,
        source_path=source_record.source_path,
        subject=subject,
        processed_at=processed_at,
        index=index,
    )

    if not message_id:
        result = SyncItemResult(
            status="skipped",
            message_id="",
            subject=subject,
            source_path=source_record.source_path,
            processed_at=processed_at,
            error="missing message_id",
            synced_at=None,
        )
        manifest_items[key] = _manifest_entry(result, sync_target)
        return result

    sync_key = _sync_key(sync_target, message_id)
    existing = manifest_items.get(sync_key)
    if isinstance(existing, dict) and existing.get("status") == "synced":
        return SyncItemResult(
            status="skipped",
            message_id=message_id,
            subject=subject,
            source_path=source_record.source_path,
            processed_at=processed_at,
            error="already synced",
            synced_at=str(existing.get("synced_at", "")) or None,
        )

    if error_text:
        result = SyncItemResult(
            status="skipped",
            message_id=message_id,
            subject=subject,
            source_path=source_record.source_path,
            processed_at=processed_at,
            error=error_text,
            synced_at=None,
        )
        manifest_items[sync_key] = _manifest_entry(result, sync_target)
        return result

    try:
        cache_key = _field_map_cache_key(sync_target)
        field_map = field_map_cache.get(cache_key)
        if field_map is None:
            field_map = _load_target_field_map(sync_target)
            field_map_cache[cache_key] = field_map

        if _query_existing_record_by_message_id(
            sync_target=sync_target,
            message_id=message_id,
            field_map=field_map,
        ):
            result = SyncItemResult(
                status="skipped",
                message_id=message_id,
                subject=subject,
                source_path=source_record.source_path,
                processed_at=processed_at,
                error="already synced in table",
                synced_at=None,
            )
            manifest_items[sync_key] = _manifest_entry(result, sync_target)
            return result

        _write_records_to_sync_target(sync_target=sync_target, records=[payload], field_map=field_map)
    except Exception as exc:
        result = SyncItemResult(
            status="failed",
            message_id=message_id,
            subject=subject,
            source_path=source_record.source_path,
            processed_at=processed_at,
            error=str(exc),
            synced_at=None,
        )
        manifest_items[sync_key] = _manifest_entry(result, sync_target)
        return result

    result = SyncItemResult(
        status="synced",
        message_id=message_id,
        subject=subject,
        source_path=source_record.source_path,
        processed_at=processed_at,
        error="",
        synced_at=datetime.now(timezone.utc).isoformat(),
    )
    manifest_items[sync_key] = _manifest_entry(result, sync_target)
    return result


def _aggregate_sync_results(results: list[tuple[SyncTarget, SyncItemResult]]) -> SyncItemResult:
    """把“同一封邮件写多张表”的结果聚合成一个总结果。

    规则：
    - 只要有一张表失败，整封邮件就是 failed。
    - 必须所有目标表都成功，整封邮件才算 synced。
    - 否则就是 skipped。
    """
    if not results:
        raise ValueError("no DingTalk sync results to aggregate")

    first_result = results[0][1]
    synced_at = next((result.synced_at for _, result in results if result.synced_at), None)

    failed_errors = [
        f"{sync_target.table_id}: {result.error or 'sync failed'}"
        for sync_target, result in results
        if result.status == "failed"
    ]
    if failed_errors:
        return SyncItemResult(
            status="failed",
            message_id=first_result.message_id,
            subject=first_result.subject,
            source_path=first_result.source_path,
            processed_at=first_result.processed_at,
            error="; ".join(failed_errors),
            synced_at=synced_at,
        )

    if all(result.status == "synced" for _, result in results):
        return SyncItemResult(
            status="synced",
            message_id=first_result.message_id,
            subject=first_result.subject,
            source_path=first_result.source_path,
            processed_at=first_result.processed_at,
            error="",
            synced_at=synced_at,
        )

    skip_errors = [result.error for _, result in results if result.error]
    return SyncItemResult(
        status="skipped",
        message_id=first_result.message_id,
        subject=first_result.subject,
        source_path=first_result.source_path,
        processed_at=first_result.processed_at,
        error="; ".join(dict.fromkeys(skip_errors)),
        synced_at=synced_at,
    )


def sync_analysis_records(
    records: list[SyncSourceRecord],
    *,
    config_path: Path,
    manifest_path: Path,
) -> SyncBatchResult:
    """同步一批分析结果到一个或多个钉钉表。"""
    manifest = load_sync_manifest(manifest_path)
    manifest_items = manifest.setdefault("items", {})
    if not isinstance(manifest_items, dict):
        raise ValueError("manifest items must be an object")

    sync_targets = _build_sync_targets(config_path=config_path)
    if not sync_targets:
        raise ValueError("no DingTalk sync targets configured")

    field_map_cache: dict[tuple[str, str], dict[str, dict[str, object]]] = {}
    items: list[SyncItemResult] = []
    synced = 0
    skipped = 0
    failed = 0

    for index, source_record in enumerate(records):
        per_target_results = [
            (
                sync_target,
                _sync_source_record(
                    source_record,
                    sync_target=sync_target,
                    field_map_cache=field_map_cache,
                    manifest_items=manifest_items,
                    index=index,
                ),
            )
            for sync_target in sync_targets
        ]

        item_result = _aggregate_sync_results(per_target_results)
        items.append(item_result)
        if item_result.status == "synced":
            synced += 1
        elif item_result.status == "skipped":
            skipped += 1
        else:
            failed += 1

    save_sync_manifest(manifest_path, manifest)
    return SyncBatchResult(total=len(records), synced=synced, skipped=skipped, failed=failed, items=items)


def sync_analysis_record(
    record: dict[str, Any],
    *,
    config_path: Path,
    manifest_path: Path,
    source_path: Path | None = None,
) -> SyncItemResult:
    """同步单条分析结果。

    参数：
    - `record`：单封邮件的分析结果 JSON。
    - `config_path`：钉钉配置文件路径。
    - `manifest_path`：同步清单文件路径。
    - `source_path`：分析 artifact 文件路径，可选。

    返回：
    - `SyncItemResult`：这封邮件对所有目标表综合后的最终状态。
    """
    batch_result = sync_analysis_records(
        [SyncSourceRecord(payload=record, source_path=source_path)],
        config_path=config_path,
        manifest_path=manifest_path,
    )
    return batch_result.items[0]
