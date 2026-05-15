"""Task pipeline tests."""

from __future__ import annotations

from pathlib import Path

from weekly_report_platform.domain.mail_analysis.service import MailAnalysisItemResult
from weekly_report_platform.runtime import TaskManager, TaskRequest, TaskState


def _build_task(workspace_tmp_path: Path) -> TaskState:
    return TaskState(
        run_id="run_test_orchestrator",
        params=TaskRequest(start_date="2026-04-27"),
        workspace_root=workspace_tmp_path / "runs" / "run_test_orchestrator",
        mail_output_dir=workspace_tmp_path / "runs" / "run_test_orchestrator" / "mail" / "risk_json",
        sync_manifest_path=workspace_tmp_path / "runs" / "run_test_orchestrator" / "sync" / "sync_manifest.json",
    )


def _build_manager(workspace_tmp_path: Path, task: TaskState) -> TaskManager:
    manager = TaskManager(
        runs_root=workspace_tmp_path / "runs",
        config_path=Path("config.local.json"),
        processed_state_path=workspace_tmp_path / ".state" / "processed_message_ids.json",
    )
    manager._tasks[task.run_id] = task
    manager._active_run_id = task.run_id
    manager._stop_events[task.run_id] = __import__("threading").Event()
    return manager


def test_orchestrator_marks_item_failed_when_analysis_artifact_is_corrupted(
    monkeypatch, workspace_tmp_path: Path
) -> None:
    task = _build_task(workspace_tmp_path)
    manager = _build_manager(workspace_tmp_path, task)
    broken_output = task.mail_output_dir / "msg-1.json"
    broken_output.parent.mkdir(parents=True, exist_ok=True)
    broken_output.write_text("{invalid json", encoding="utf-8")

    async def fake_analyze_email(**_: object) -> MailAnalysisItemResult:
        return MailAnalysisItemResult(
            message_id="<msg-1>",
            subject="周报A",
            payload=None,
            output_path=broken_output,
        )

    monkeypatch.setattr(
        "weekly_report_platform.runtime.mail_pipeline_service.fetch_candidate_emails",
        lambda **_: [{"id": "1", "message_id": "<msg-1>", "subject": "周报A"}],
    )
    monkeypatch.setattr(
        "weekly_report_platform.runtime.mail_pipeline_service.analyze_email",
        fake_analyze_email,
    )
    monkeypatch.setattr(
        "weekly_report_platform.runtime.sync_service.sync_analysis_record",
        lambda *_, **__: (_ for _ in ()).throw(AssertionError("corrupted payload should not sync")),
    )

    manager._run_task(task.run_id)
    updated_task = manager.get_task(task.run_id)

    assert updated_task is not None
    assert updated_task.items[0].status == "failed"
    assert updated_task.items[0].reason == "分析结果损坏"


def test_orchestrator_marks_item_failed_when_sync_raises(monkeypatch, workspace_tmp_path: Path) -> None:
    task = _build_task(workspace_tmp_path)
    manager = _build_manager(workspace_tmp_path, task)

    async def fake_analyze_email(**_: object) -> MailAnalysisItemResult:
        return MailAnalysisItemResult(
            message_id="<msg-1>",
            subject="周报A",
            payload={
                "message_id": "<msg-1>",
                "subject": "周报A",
                "processed_at": "2026-04-30T08:00:00+08:00",
                "analysis": {},
                "errors": "",
            },
            output_path=task.mail_output_dir / "msg-1.json",
        )

    monkeypatch.setattr(
        "weekly_report_platform.runtime.mail_pipeline_service.fetch_candidate_emails",
        lambda **_: [{"id": "1", "message_id": "<msg-1>", "subject": "周报A"}],
    )
    monkeypatch.setattr(
        "weekly_report_platform.runtime.mail_pipeline_service.analyze_email",
        fake_analyze_email,
    )
    monkeypatch.setattr(
        "weekly_report_platform.runtime.sync_service.sync_analysis_record",
        lambda *_, **__: (_ for _ in ()).throw(RuntimeError("sync failed")),
    )

    manager._run_task(task.run_id)
    updated_task = manager.get_task(task.run_id)

    assert updated_task is not None
    assert updated_task.items[0].status == "failed"
    assert updated_task.items[0].reason == "同步失败"


def test_orchestrator_reprocesses_message_with_force_refresh(monkeypatch, workspace_tmp_path: Path) -> None:
    task = _build_task(workspace_tmp_path)
    processed_state_path = workspace_tmp_path / ".state" / "processed_message_ids.json"
    manager = TaskManager(
        runs_root=workspace_tmp_path / "runs",
        config_path=Path("config.local.json"),
        processed_state_path=processed_state_path,
    )
    manager._tasks[task.run_id] = task
    manager._active_run_id = task.run_id
    manager._stop_events[task.run_id] = __import__("threading").Event()
    processed_state_path.parent.mkdir(parents=True, exist_ok=True)
    processed_state_path.write_text('["<msg-1>"]', encoding="utf-8")

    async def fake_analyze_email(**_: object) -> MailAnalysisItemResult:
        return MailAnalysisItemResult(
            message_id="<msg-1>",
            subject="周报A",
            payload={
                "message_id": "<msg-1>",
                "subject": "周报A",
                "processed_at": "2026-04-30T08:00:00+08:00",
                "analysis": {},
                "errors": "",
            },
            output_path=task.mail_output_dir / "msg-1.json",
        )

    monkeypatch.setattr(
        "weekly_report_platform.runtime.mail_pipeline_service.fetch_candidate_emails",
        lambda **_: [{"id": "1", "message_id": "<msg-1>", "subject": "周报A"}],
    )
    monkeypatch.setattr(
        "weekly_report_platform.runtime.mail_pipeline_service.analyze_email",
        fake_analyze_email,
    )
    monkeypatch.setattr(
        "weekly_report_platform.runtime.sync_service.sync_analysis_record",
        lambda *_, **__: type("Result", (), {"status": "synced"})(),
    )

    manager._run_task(task.run_id)
    updated_task = manager.get_task(task.run_id)

    assert updated_task is not None
    assert updated_task.items[0].status == "synced"


def test_orchestrator_can_stop_after_current_item(monkeypatch, workspace_tmp_path: Path) -> None:
    task = _build_task(workspace_tmp_path)
    manager = _build_manager(workspace_tmp_path, task)
    stop_called = {"done": False}

    async def fake_analyze_email(**_: object) -> MailAnalysisItemResult:
        if not stop_called["done"]:
            manager.stop_task(task.run_id)
            stop_called["done"] = True
        return MailAnalysisItemResult(
            message_id="<msg-1>",
            subject="周报A",
            payload={
                "message_id": "<msg-1>",
                "subject": "周报A",
                "processed_at": "2026-04-30T08:00:00+08:00",
                "analysis": {},
                "errors": "",
            },
            output_path=task.mail_output_dir / "msg-1.json",
        )

    monkeypatch.setattr(
        "weekly_report_platform.runtime.mail_pipeline_service.fetch_candidate_emails",
        lambda **_: [
            {"id": "1", "message_id": "<msg-1>", "subject": "周报A"},
            {"id": "2", "message_id": "<msg-2>", "subject": "周报B"},
        ],
    )
    monkeypatch.setattr(
        "weekly_report_platform.runtime.mail_pipeline_service.analyze_email",
        fake_analyze_email,
    )
    monkeypatch.setattr(
        "weekly_report_platform.runtime.sync_service.sync_analysis_record",
        lambda *_, **__: type("Result", (), {"status": "synced"})(),
    )

    manager._run_task(task.run_id)
    updated_task = manager.get_task(task.run_id)

    assert updated_task is not None
    assert updated_task.status == "stopped"
    assert updated_task.items[0].status == "synced"
    assert updated_task.items[1].status == "queued"
