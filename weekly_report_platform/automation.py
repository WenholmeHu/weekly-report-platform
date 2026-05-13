"""自动拉取控制器。"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from weekly_report_platform.runtime import TaskManager


def _coerce_optional_positive_int(value: object) -> int | None:
    """把可选数量配置统一成正整数或 None。"""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    parsed = int(value)
    return parsed if parsed > 0 else None


@dataclass
class AutomationConfig:
    """自动拉取配置。

    这里只保留“怎么拉取”的参数：
    - 从哪一天开始看邮件
    - 主题关键字
    - 轮询间隔
    - 是否强制重跑
    """

    interval_seconds: int = 300
    start_date: str = "2026-05-01"
    subject_keyword: str = "周报"
    force_refresh: bool = False
    max_emails: int | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "AutomationConfig":
        return cls(
            interval_seconds=max(int(payload.get("interval_seconds", 300)), 1),
            start_date=str(payload.get("start_date", "2026-05-01")),
            subject_keyword=str(payload.get("subject_keyword", "周报")),
            force_refresh=bool(payload.get("force_refresh", False)),
            max_emails=_coerce_optional_positive_int(
                payload.get(
                    "max_emails",
                    payload.get("extract_count", payload.get("extraction_count", payload.get("mail_limit"))),
                )
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class AutomationController:
    """管理自动拉取配置和后台轮询线程。"""

    def __init__(self, *, task_manager: TaskManager, state_path: Path) -> None:
        self._task_manager = task_manager
        self._state_path = state_path
        self._lock = threading.Lock()
        self._config, self._auto_running = self._load_state()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        if self._auto_running:
            self._start_scheduler_if_needed()

    def _load_state(self) -> tuple[AutomationConfig, bool]:
        if not self._state_path.exists():
            return AutomationConfig(), False

        payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("automation config state must be a JSON object")

        # 允许从旧的“只存 config”格式平滑恢复，避免升级后直接崩。
        if "config" not in payload:
            return AutomationConfig.from_dict(payload), False

        config_payload = payload.get("config")
        if not isinstance(config_payload, dict):
            raise ValueError("automation state 'config' must be a JSON object")
        return AutomationConfig.from_dict(config_payload), bool(payload.get("auto_running", False))

    def _save_state(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "config": self._config.to_dict(),
            "auto_running": self._auto_running,
        }
        self._state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _build_task_payload(self) -> dict[str, object]:
        with self._lock:
            config = self._config
        return {
            "start_date": config.start_date,
            "subject_keyword": config.subject_keyword,
            "force_refresh": config.force_refresh,
            "max_emails": config.max_emails,
        }

    def _start_scheduler_if_needed(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event = threading.Event()
            worker = threading.Thread(target=self._loop, name="automation-scheduler", daemon=True)
            self._thread = worker
            worker.start()

    def _stop_scheduler_worker(self) -> None:
        worker: threading.Thread | None = None
        with self._lock:
            self._stop_event.set()
            worker = self._thread
        if worker is not None and worker.is_alive():
            worker.join(timeout=1.0)
        with self._lock:
            if self._thread is worker:
                self._thread = None

    def _start_task_now(self) -> dict[str, object] | None:
        task = self._task_manager.start_task(self._build_task_payload())
        if task is None:
            return None
        return task.to_dict()

    def _get_interval_seconds(self) -> int:
        with self._lock:
            return self._config.interval_seconds

    def get_state(self) -> dict[str, object]:
        active_task = self._task_manager.get_active_task()
        with self._lock:
            return {
                "config": self._config.to_dict(),
                "running": self._auto_running,
                "active_task": active_task.to_dict() if active_task is not None else None,
            }

    def update_config(self, payload: dict[str, object]) -> dict[str, object]:
        """保存配置，并立即开始一轮处理，同时开启后续自动轮询。"""
        with self._lock:
            self._config = AutomationConfig.from_dict(payload)
            self._auto_running = True
            self._save_state()

        started_task = self._start_task_now()
        self._start_scheduler_if_needed()

        state = self.get_state()
        state["started_task"] = started_task
        return state

    def stop(self) -> dict[str, object]:
        """停止后续自动拉取，不强杀当前正在运行的任务。"""
        with self._lock:
            self._auto_running = False
            self._save_state()
        self._stop_scheduler_worker()
        return self.get_state()

    def _loop(self) -> None:
        """定时线程。

        规则：
        - 当前没有后台任务时，启动下一轮。
        - 间隔时间由 `interval_seconds` 决定。
        """
        while not self._stop_event.is_set():
            try:
                active_task = self._task_manager.get_active_task()
                if active_task is None:
                    self._task_manager.start_task(self._build_task_payload())
                wait_seconds = self._get_interval_seconds()
            except Exception:
                wait_seconds = self._get_interval_seconds()
            if self._stop_event.wait(timeout=float(wait_seconds)):
                break
        with self._lock:
            self._thread = None
