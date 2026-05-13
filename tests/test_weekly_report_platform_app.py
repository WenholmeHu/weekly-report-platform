"""API app tests."""

from pathlib import Path

from fastapi.testclient import TestClient

from weekly_report_platform.api.app import create_app
from weekly_report_platform.runtime import TaskRequest, TaskState


def _build_task(status: str = "pending") -> TaskState:
    return TaskState(
        run_id="run_test_new_pkg",
        params=TaskRequest(start_date="2026-04-27"),
        workspace_root=Path("D:/tmp/run_test_new_pkg"),
        mail_output_dir=Path("D:/tmp/run_test_new_pkg/mail/risk_json"),
        sync_manifest_path=Path("D:/tmp/run_test_new_pkg/sync/sync_manifest.json"),
        status=status,
    )


class IdleStubTaskManager:
    def __init__(self) -> None:
        self.task = _build_task(status="pending")
        self.last_payload: dict[str, object] | None = None

    def start_task(self, payload: dict[str, object]) -> TaskState | None:
        self.last_payload = payload
        return self.task

    def stop_active_task(self) -> TaskState | None:
        return None

    def get_task(self, run_id: str) -> TaskState | None:
        if run_id == self.task.run_id:
            return self.task
        return None

    def get_active_task(self) -> TaskState | None:
        return None


class ActiveStubTaskManager(IdleStubTaskManager):
    def __init__(self) -> None:
        super().__init__()
        self.task.status = "running"

    def start_task(self, payload: dict[str, object]) -> TaskState | None:
        self.last_payload = payload
        return None

    def stop_active_task(self) -> TaskState | None:
        self.task.status = "stopping"
        return self.task

    def get_active_task(self) -> TaskState | None:
        return self.task


def test_create_app_serves_frontend_and_task_status(monkeypatch) -> None:
    monkeypatch.setattr("weekly_report_platform.api.app.task_manager", IdleStubTaskManager())
    client = TestClient(create_app())

    root_response = client.get("/")
    assert root_response.status_code == 200
    assert "text/html" in root_response.headers["content-type"]

    task_response = client.get("/api/tasks/run_test_new_pkg")
    assert task_response.status_code == 200
    assert task_response.json()["run_id"] == "run_test_new_pkg"


def test_frontend_allows_empty_start_date_and_places_stop_button_below_start(monkeypatch) -> None:
    monkeypatch.setattr("weekly_report_platform.api.app.task_manager", IdleStubTaskManager())
    client = TestClient(create_app())

    html = client.get("/").text
    start_input = '<input type="date" name="start_date"'
    assert start_input in html
    assert "name=\"start_date\" required" not in html
    assert html.index('id="submit-button"') < html.index('id="stop-task-button"')


def test_create_app_manual_start_uses_request_payload(monkeypatch) -> None:
    manager = IdleStubTaskManager()
    monkeypatch.setattr("weekly_report_platform.api.app.task_manager", manager)
    client = TestClient(create_app())

    response = client.post(
        "/api/tasks",
        json={
            "start_date": "2026-05-03",
            "subject_keyword": "项目周报",
            "force_refresh": True,
            "max_emails": 1,
        },
    )

    assert response.status_code == 202
    assert manager.last_payload == {
        "start_date": "2026-05-03",
        "subject_keyword": "项目周报",
        "force_refresh": True,
        "max_emails": 1,
    }


def test_create_app_manual_start_accepts_empty_optional_filters(monkeypatch) -> None:
    manager = IdleStubTaskManager()
    monkeypatch.setattr("weekly_report_platform.api.app.task_manager", manager)
    client = TestClient(create_app())

    response = client.post(
        "/api/tasks",
        json={
            "start_date": "",
            "subject_keyword": "",
            "force_refresh": False,
            "max_emails": None,
        },
    )

    assert response.status_code == 202
    assert manager.last_payload == {
        "start_date": "",
        "subject_keyword": "",
        "force_refresh": False,
        "max_emails": None,
    }


def test_create_app_returns_409_when_another_task_is_running(monkeypatch) -> None:
    active_manager = ActiveStubTaskManager()
    monkeypatch.setattr("weekly_report_platform.api.app.task_manager", active_manager)
    client = TestClient(create_app())

    response = client.post("/api/tasks", json={})

    assert response.status_code == 409
    assert response.json()["active_task"]["run_id"] == "run_test_new_pkg"


def test_create_app_can_stop_active_task(monkeypatch) -> None:
    active_manager = ActiveStubTaskManager()
    monkeypatch.setattr("weekly_report_platform.api.app.task_manager", active_manager)
    client = TestClient(create_app())

    response = client.post("/api/tasks/stop")

    assert response.status_code == 200
    assert response.json()["status"] == "stopping"


def test_create_app_no_longer_exposes_automation_routes(monkeypatch) -> None:
    monkeypatch.setattr("weekly_report_platform.api.app.task_manager", IdleStubTaskManager())
    client = TestClient(create_app())

    assert client.get("/api/automation").status_code == 404
    assert client.put("/api/automation", json={}).status_code == 404
    assert client.post("/api/automation/stop").status_code == 404
