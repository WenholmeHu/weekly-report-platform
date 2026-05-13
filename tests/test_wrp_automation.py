"""Automation controller tests."""

import json
from pathlib import Path

from weekly_report_platform.automation import AutomationController
from weekly_report_platform.runtime import TaskRequest, TaskState


class StubTaskManager:
    def __init__(self) -> None:
        self.started_payloads: list[dict[str, object]] = []
        self.active_task: TaskState | None = None

    def start_task(self, payload: dict[str, object]) -> TaskState | None:
        self.started_payloads.append(payload)
        task = TaskState(
            run_id=f"run_{len(self.started_payloads)}",
            params=TaskRequest(start_date=str(payload["start_date"])),
            workspace_root=Path("D:/tmp/run"),
            mail_output_dir=Path("D:/tmp/run/mail/risk_json"),
            sync_manifest_path=Path("D:/tmp/run/sync/sync_manifest.json"),
            status="pending",
        )
        self.active_task = task
        return task

    def get_active_task(self) -> TaskState | None:
        return self.active_task


def test_automation_update_config_always_starts_task_and_scheduler(monkeypatch, workspace_tmp_path: Path) -> None:
    manager = StubTaskManager()
    controller = AutomationController(task_manager=manager, state_path=workspace_tmp_path / "automation.json")
    started = {"scheduler": 0}

    monkeypatch.setattr(
        controller,
        "_start_scheduler_if_needed",
        lambda: started.__setitem__("scheduler", started["scheduler"] + 1),
    )

    state = controller.update_config(
        {
            "interval_seconds": 180,
            "start_date": "2026-05-01",
            "subject_keyword": "周报",
            "force_refresh": False,
            "max_emails": 1,
        }
    )

    assert manager.started_payloads == [
        {
            "start_date": "2026-05-01",
            "subject_keyword": "周报",
            "force_refresh": False,
            "max_emails": 1,
        }
    ]
    assert state["started_task"]["run_id"] == "run_1"
    assert started["scheduler"] == 1


def test_automation_stop_only_stops_scheduler_and_persists_state(monkeypatch, workspace_tmp_path: Path) -> None:
    manager = StubTaskManager()
    state_path = workspace_tmp_path / "automation.json"
    controller = AutomationController(task_manager=manager, state_path=state_path)
    stopped = {"scheduler": 0}

    controller.update_config(
        {
            "interval_seconds": 180,
            "start_date": "2026-05-01",
            "subject_keyword": "周报",
            "force_refresh": True,
        }
    )

    monkeypatch.setattr(
        controller,
        "_stop_scheduler_worker",
        lambda: stopped.__setitem__("scheduler", stopped["scheduler"] + 1),
    )

    state = controller.stop()
    payload = json.loads(state_path.read_text(encoding="utf-8"))

    assert stopped["scheduler"] == 1
    assert state["config"]["interval_seconds"] == 180
    assert payload["auto_running"] is False
    assert state["running"] is False


def test_automation_controller_restarts_scheduler_when_saved_state_is_running(monkeypatch, workspace_tmp_path: Path) -> None:
    state_path = workspace_tmp_path / "automation.json"
    state_path.write_text(
        json.dumps(
            {
                "config": {
                    "interval_seconds": 240,
                    "start_date": "2026-05-02",
                    "subject_keyword": "周报",
                    "force_refresh": False,
                    "max_emails": 2,
                },
                "auto_running": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    started = {"scheduler": 0}
    monkeypatch.setattr(
        AutomationController,
        "_start_scheduler_if_needed",
        lambda self: started.__setitem__("scheduler", started["scheduler"] + 1),
    )

    controller = AutomationController(task_manager=StubTaskManager(), state_path=state_path)

    assert started["scheduler"] == 1
    assert controller.get_state()["config"]["interval_seconds"] == 240
    assert controller.get_state()["config"]["max_emails"] == 2
    assert controller.get_state()["running"] is True
