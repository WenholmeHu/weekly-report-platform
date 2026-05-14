"""钉钉同步服务测试。"""

import json
from pathlib import Path

import pytest

from weekly_report_platform import dingtalk as sync_service


def _sync_key(
    *,
    base_id: str = "base-1",
    table_id: str = "table-1",
    message_id: str = "<msg-1>",
) -> str:
    return f"base::{base_id}::table::{table_id}::message::{message_id}"


def _target(
    *,
    base_id: str = "base-1",
    table_id: str = "table-1",
) -> sync_service.SyncTarget:
    return sync_service.SyncTarget(
        base_id=base_id,
        table_id=table_id,
        fields={},
        mcp_url="https://example.com/mcp",
    )


def test_wrp_sync_analysis_record_returns_synced_result_and_updates_manifest(monkeypatch, workspace_tmp_path: Path) -> None:
    observed: dict[str, object] = {}

    def fake_write_records_to_sync_target(
        *, sync_target: sync_service.SyncTarget, records: list[dict], field_map: dict | None = None
    ) -> int:
        observed["sync_target"] = sync_target
        observed["records"] = records
        return len(records)

    monkeypatch.setattr(sync_service, "_write_records_to_sync_target", fake_write_records_to_sync_target)
    monkeypatch.setattr(sync_service, "_build_sync_targets", lambda **_: [_target()])
    monkeypatch.setattr(sync_service, "_load_target_field_map", lambda *_: {"subject": {"fieldId": "fld_subject"}})
    monkeypatch.setattr(sync_service, "_query_existing_record_by_message_id", lambda **_: False)

    manifest_path = workspace_tmp_path / "runs" / "run_1" / "sync" / "sync_manifest.json"
    source_path = workspace_tmp_path / "runs" / "run_1" / "mail" / "risk_json" / "msg-1.json"
    record = {
        "message_id": "<msg-1>",
        "subject": "周报A",
        "processed_at": "2026-04-30T08:00:00+08:00",
        "analysis": {},
        "errors": "",
    }

    result = sync_service.sync_analysis_record(
        record,
        config_path=Path("config.local.json"),
        manifest_path=manifest_path,
        source_path=source_path,
    )

    assert result.status == "synced"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["items"][_sync_key()]["status"] == "synced"
    assert observed["records"] == [record]
    assert observed["sync_target"].table_id == "table-1"


def test_wrp_sync_analysis_records_skips_already_synced_message_id(monkeypatch, workspace_tmp_path: Path) -> None:
    monkeypatch.setattr(sync_service, "_build_sync_targets", lambda **_: [_target()])
    monkeypatch.setattr(
        sync_service,
        "_write_records_to_sync_target",
        lambda **_: (_ for _ in ()).throw(AssertionError("should not write duplicated record")),
    )
    manifest_path = workspace_tmp_path / "sync" / "sync_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "items": {
                    _sync_key(): {
                        "status": "synced",
                        "source_file": "old.json",
                        "base_id": "base-1",
                        "table_id": "table-1",
                        "message_id": "<msg-1>",
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    result = sync_service.sync_analysis_records(
        [
            sync_service.SyncSourceRecord(
                payload={
                    "message_id": "<msg-1>",
                    "subject": "周报A",
                    "processed_at": "2026-04-30T08:00:00+08:00",
                    "analysis": {},
                    "errors": "",
                },
                source_path=workspace_tmp_path / "risk_json" / "msg-1.json",
            )
        ],
        config_path=Path("config.local.json"),
        manifest_path=manifest_path,
    )

    assert result.skipped == 1
    assert result.items[0].status == "skipped"


def test_wrp_sync_analysis_records_allows_same_message_id_for_different_tables(
    monkeypatch, workspace_tmp_path: Path
) -> None:
    writes: list[tuple[str, list[dict]]] = []

    def fake_write_records_to_sync_target(
        *, sync_target: sync_service.SyncTarget, records: list[dict], field_map: dict | None = None
    ) -> int:
        writes.append((sync_target.table_id, records))
        return len(records)

    monkeypatch.setattr(sync_service, "_write_records_to_sync_target", fake_write_records_to_sync_target)
    monkeypatch.setattr(sync_service, "_build_sync_targets", lambda **_: [_target(), _target(table_id="table-2")])
    monkeypatch.setattr(sync_service, "_load_target_field_map", lambda *_: {"subject": {"fieldId": "fld_subject"}})
    monkeypatch.setattr(sync_service, "_query_existing_record_by_message_id", lambda **_: False)

    manifest_path = workspace_tmp_path / "sync" / "sync_manifest.json"
    record = sync_service.SyncSourceRecord(
        payload={
            "message_id": "<msg-1>",
            "subject": "周报A",
            "processed_at": "2026-04-30T08:00:00+08:00",
            "analysis": {},
            "errors": "",
        },
        source_path=workspace_tmp_path / "risk_json" / "msg-1.json",
    )

    result = sync_service.sync_analysis_records(
        [record],
        config_path=Path("config.local.json"),
        manifest_path=manifest_path,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert result.synced == 1
    assert len(writes) == 2
    assert _sync_key(table_id="table-1") in manifest["items"]
    assert _sync_key(table_id="table-2") in manifest["items"]


def test_wrp_sync_analysis_records_marks_item_failed_when_any_target_fails(
    monkeypatch, workspace_tmp_path: Path
) -> None:
    def fake_write_records_to_sync_target(
        *, sync_target: sync_service.SyncTarget, records: list[dict], field_map: dict | None = None
    ) -> int:
        if sync_target.table_id == "table-2":
            raise RuntimeError("downstream rejected record")
        return len(records)

    monkeypatch.setattr(sync_service, "_write_records_to_sync_target", fake_write_records_to_sync_target)
    monkeypatch.setattr(sync_service, "_build_sync_targets", lambda **_: [_target(), _target(table_id="table-2")])
    monkeypatch.setattr(sync_service, "_load_target_field_map", lambda *_: {"subject": {"fieldId": "fld_subject"}})
    monkeypatch.setattr(sync_service, "_query_existing_record_by_message_id", lambda **_: False)

    manifest_path = workspace_tmp_path / "sync" / "sync_manifest.json"
    result = sync_service.sync_analysis_records(
        [
            sync_service.SyncSourceRecord(
                payload={
                    "message_id": "<msg-1>",
                    "subject": "周报A",
                    "processed_at": "2026-04-30T08:00:00+08:00",
                    "analysis": {},
                    "errors": "",
                },
                source_path=workspace_tmp_path / "risk_json" / "msg-1.json",
            )
        ],
        config_path=Path("config.local.json"),
        manifest_path=manifest_path,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert result.failed == 1
    assert result.items[0].status == "failed"
    assert "table-2: downstream rejected record" in result.items[0].error
    assert manifest["items"][_sync_key(table_id="table-1")]["status"] == "synced"
    assert manifest["items"][_sync_key(table_id="table-2")]["status"] == "failed"


def test_wrp_sync_analysis_records_skips_when_message_id_already_exists_in_table(
    monkeypatch, workspace_tmp_path: Path
) -> None:
    monkeypatch.setattr(sync_service, "_build_sync_targets", lambda **_: [_target()])
    monkeypatch.setattr(sync_service, "_load_target_field_map", lambda *_: {"message_id": {"fieldId": "fld_mid"}})
    monkeypatch.setattr(sync_service, "_query_existing_record_by_message_id", lambda **_: True)
    monkeypatch.setattr(
        sync_service,
        "_write_records_to_sync_target",
        lambda **_: (_ for _ in ()).throw(AssertionError("should not write duplicate table record")),
    )

    manifest_path = workspace_tmp_path / "sync" / "sync_manifest.json"
    result = sync_service.sync_analysis_records(
        [
            sync_service.SyncSourceRecord(
                payload={
                    "message_id": "<msg-1>",
                    "subject": "周报A",
                    "processed_at": "2026-04-30T08:00:00+08:00",
                    "analysis": {},
                    "errors": "",
                },
                source_path=workspace_tmp_path / "risk_json" / "msg-1.json",
            )
        ],
        config_path=Path("config.local.json"),
        manifest_path=manifest_path,
    )

    assert result.skipped == 1
    assert result.items[0].status == "skipped"
    assert result.items[0].error == "already synced in table"


def test_wrp_sync_analysis_records_loads_target_field_map_only_once_per_target(
    monkeypatch, workspace_tmp_path: Path
) -> None:
    load_calls: list[str] = []

    monkeypatch.setattr(sync_service, "_build_sync_targets", lambda **_: [_target()])
    monkeypatch.setattr(
        sync_service,
        "_load_target_field_map",
        lambda sync_target: (load_calls.append(sync_target.table_id) or {"subject": {"fieldId": "fld_subject"}}),
    )
    monkeypatch.setattr(sync_service, "_query_existing_record_by_message_id", lambda **_: False)
    monkeypatch.setattr(sync_service, "_write_records_to_sync_target", lambda **_: 1)

    manifest_path = workspace_tmp_path / "sync" / "sync_manifest.json"
    records = [
        sync_service.SyncSourceRecord(
            payload={
                "message_id": "<msg-1>",
                "subject": "周报A",
                "processed_at": "2026-04-30T08:00:00+08:00",
                "analysis": {},
                "errors": "",
            },
            source_path=workspace_tmp_path / "risk_json" / "msg-1.json",
        ),
        sync_service.SyncSourceRecord(
            payload={
                "message_id": "<msg-2>",
                "subject": "周报B",
                "processed_at": "2026-04-30T08:00:01+08:00",
                "analysis": {},
                "errors": "",
            },
            source_path=workspace_tmp_path / "risk_json" / "msg-2.json",
        ),
    ]

    result = sync_service.sync_analysis_records(
        records,
        config_path=Path("config.local.json"),
        manifest_path=manifest_path,
    )

    assert result.synced == 2
    assert load_calls == ["table-1"]


def test_wrp_sync_analysis_records_does_not_reuse_field_map_across_different_bases_with_same_table_id(
    monkeypatch, workspace_tmp_path: Path
) -> None:
    load_calls: list[tuple[str, str]] = []
    writes: list[tuple[str, str, dict | None]] = []

    monkeypatch.setattr(
        sync_service,
        "_build_sync_targets",
        lambda **_: [
            _target(base_id="base-1", table_id="table-1"),
            _target(base_id="base-2", table_id="table-1"),
        ],
    )

    def fake_load_target_field_map(sync_target: sync_service.SyncTarget) -> dict[str, dict[str, object]]:
        load_calls.append((sync_target.base_id, sync_target.table_id))
        return {"subject": {"fieldId": f"fld_{sync_target.base_id}"}}

    def fake_write_records_to_sync_target(
        *, sync_target: sync_service.SyncTarget, records: list[dict], field_map: dict | None = None
    ) -> int:
        writes.append((sync_target.base_id, sync_target.table_id, field_map))
        return len(records)

    monkeypatch.setattr(sync_service, "_load_target_field_map", fake_load_target_field_map)
    monkeypatch.setattr(sync_service, "_query_existing_record_by_message_id", lambda **_: False)
    monkeypatch.setattr(sync_service, "_write_records_to_sync_target", fake_write_records_to_sync_target)

    manifest_path = workspace_tmp_path / "sync" / "sync_manifest.json"
    result = sync_service.sync_analysis_records(
        [
            sync_service.SyncSourceRecord(
                payload={
                    "message_id": "<msg-1>",
                    "subject": "周报A",
                    "processed_at": "2026-04-30T08:00:00+08:00",
                    "analysis": {},
                    "errors": "",
                },
                source_path=workspace_tmp_path / "risk_json" / "msg-1.json",
            )
        ],
        config_path=Path("config.local.json"),
        manifest_path=manifest_path,
    )

    assert result.synced == 1
    assert load_calls == [("base-1", "table-1"), ("base-2", "table-1")]
    assert writes[0][2] == {"subject": {"fieldId": "fld_base-1"}}
    assert writes[1][2] == {"subject": {"fieldId": "fld_base-2"}}


def test_wrp_sync_analysis_records_raises_clear_error_when_no_targets(monkeypatch, workspace_tmp_path: Path) -> None:
    monkeypatch.setattr(sync_service, "_build_sync_targets", lambda **_: [])

    with pytest.raises(ValueError, match="no DingTalk sync targets configured"):
        sync_service.sync_analysis_records(
            [],
            config_path=Path("config.local.json"),
            manifest_path=workspace_tmp_path / "sync" / "sync_manifest.json",
        )


def test_wrp_normalize_cell_value_progress_valid() -> None:
    meta = {"sourcePath": sync_service.PROGRESS_VALUE_PATH}
    assert sync_service.normalize_cell_value("项目进度", "progress", 0.75, meta) == 0.75
    assert sync_service.normalize_cell_value("项目进度", "progress", "0.5", meta) == 0.5


def test_wrp_normalize_cell_value_date_truncates() -> None:
    meta = {"sourcePath": "processed_at"}
    result = sync_service.normalize_cell_value("邮箱抽取日期", "date", "2026-05-14T08:30:00+00:00", meta)
    assert result == "2026-05-14"


def test_wrp_format_risk_section_follows_detail_fields_order() -> None:
    item = {
        "序号": 1,
        "风险类型": "技术风险",
        "风险等级": "高",
        "风险描述": "服务器延迟",
        "风险措施和最新进展": "已采购",
        "风险责任人": "张三",
        "计划解决日期": "2026-06-01",
    }
    result = sync_service.format_risk_section(item, 0, (
        sync_service.RISK_TYPE_KEY, sync_service.RISK_LEVEL_KEY, sync_service.RISK_DESC_KEY,
        sync_service.RISK_ACTION_KEY, sync_service.RISK_OWNER_KEY, sync_service.RISK_DUE_DATE_KEY,
    ))
    assert result.startswith("1. 风险类型: 技术风险")
    assert "风险等级: 高" in result
    assert "风险描述: 服务器延迟" in result
    assert "计划解决日期: 2026-06-01" in result
