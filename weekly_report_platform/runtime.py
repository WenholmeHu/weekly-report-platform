"""后端任务运行时。

这一层只解决三件事：
1. 接收前端请求，创建一次新的 run 目录。
2. 在后台线程里串行执行“拉邮件 -> 分析 -> 同步钉钉”。
3. 对外暴露任务状态，供前端轮询展示。
"""

from __future__ import annotations

import asyncio
import copy
import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from weekly_report_platform import dingtalk as sync_service
from weekly_report_platform.domain.mail_analysis import service as mail_pipeline_service
from weekly_report_platform.domain.run_workspace import service as run_workspace_service
from weekly_report_platform.infrastructure.config import DEFAULT_PROCESSED_STATE_PATH
from weekly_report_platform.processed_mail import load_processed_message_ids, mark_message_processed


def utc_now_iso() -> str:
    """返回当前 UTC 时间字符串。"""
    return datetime.now(timezone.utc).isoformat()


def _coerce_bool(value: Any) -> bool:
    """把前端或 JSON 里的布尔值统一成 bool。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "0", "false", "no", "off"}:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return True
    return bool(value)


def _coerce_optional_positive_int(value: Any) -> int | None:
    """把页面传入的可选数量解析为正整数；空值表示不限制。"""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    parsed = int(value)
    return parsed if parsed > 0 else None


def _coerce_optional_text(value: Any) -> str | None:
    """空字符串按未提供处理。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_subject_keyword(value: Any) -> str:
    """关键词为空时使用默认周报。"""
    text = _coerce_optional_text(value)
    return text or "周报"


def _item_id(email_data: dict[str, object], index: int) -> str:
    """给任务列表里的每封邮件生成页面展示 id。"""
    raw_id = str(email_data.get("id", "")).strip()
    return f"email-{raw_id}" if raw_id else f"email-{index}"


def _task_summary(items: list["TaskItemState"]) -> dict[str, int]:
    """根据每封邮件状态生成任务汇总。"""
    done_statuses = {"synced", "skipped", "failed"}
    return {
        "total_emails": len(items),
        "processed_emails": sum(1 for item in items if item.status in done_statuses),
        "synced_count": sum(1 for item in items if item.status == "synced"),
        "skipped_count": sum(1 for item in items if item.status == "skipped"),
        "failed_count": sum(1 for item in items if item.status == "failed"),
    }


def _read_analysis_payload(analysis_item: Any) -> dict[str, object]:
    """统一读取分析结果。

    上游有两种返回方式：
    - 直接把 payload 带在内存对象里。
    - 只给出 output_path，让下游自己回读 JSON artifact。
    """
    if analysis_item.payload is not None:
        return analysis_item.payload
    if analysis_item.output_path is None:
        raise ValueError("analysis result does not contain payload or output path")

    payload = json.loads(Path(analysis_item.output_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("analysis artifact payload must be a JSON object")
    return payload


def _display_skip_reason(error_text: str) -> str:
    """把内部跳过原因翻译成页面更容易读懂的中文。"""
    normalized = error_text.strip()
    lowered = normalized.lower()
    if lowered == "already processed locally":
        return "本地已处理过"
    if lowered == "already synced":
        return "本轮已经同步过"
    if lowered == "already synced in table":
        return "钉钉表中已存在"
    if lowered == "missing message_id":
        return "缺少邮件标识"
    return normalized[:80] if normalized else "已跳过"


@dataclass(frozen=True)
class TaskRequest:
    """一次任务的入参。"""

    start_date: str | None
    subject_keyword: str = "周报"
    force_refresh: bool = True
    max_emails: int | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TaskRequest":
        return cls(
            start_date=_coerce_optional_text(payload.get("start_date")),
            subject_keyword=_coerce_subject_keyword(payload.get("subject_keyword", "周报")),
            force_refresh=True,
            max_emails=_coerce_optional_positive_int(
                payload.get(
                    "max_emails",
                    payload.get("extract_count", payload.get("extraction_count", payload.get("mail_limit"))),
                )
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskItemState:
    """任务中的单封邮件状态。"""

    item_id: str
    message_id: str = ""
    subject: str = ""
    status: str = "queued"
    reason: str = ""


@dataclass
class TaskState:
    """一次完整后台任务的状态快照。"""

    run_id: str
    params: TaskRequest
    workspace_root: Path
    mail_output_dir: Path
    sync_manifest_path: Path
    status: str = "pending"
    created_at: str = field(default_factory=utc_now_iso)
    started_at: str | None = None
    finished_at: str | None = None
    error_message: str = ""
    stop_requested: bool = False
    items: list[TaskItemState] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        current_item = None
        failed_items: list[dict[str, str]] = []
        skipped_items: list[dict[str, str]] = []

        for item in self.items:
            if item.status in {"analyzing", "syncing"}:
                current_item = {
                    "subject": item.subject or "未命名邮件",
                    "stage": "信息提取中" if item.status == "analyzing" else "钉钉表同步中",
                }
            if item.status == "failed":
                failed_items.append({
                    "subject": item.subject or "未命名邮件",
                    "reason": item.reason or "未知原因",
                })
            if item.status == "skipped":
                skipped_items.append({
                    "subject": item.subject or "未命名邮件",
                    "reason": item.reason or "已跳过",
                })

        return {
            "run_id": self.run_id,
            "status": self.status,
            "summary": _task_summary(self.items),
            "current_item": current_item,
            "failed_items": failed_items,
            "skipped_items": skipped_items,
        }


class TaskManager:
    """管理当前唯一可运行的后台任务。"""

    def __init__(
        self,
        *,
        runs_root: Path,
        config_path: Path,
        processed_state_path: Path = DEFAULT_PROCESSED_STATE_PATH,
    ) -> None:
        self._runs_root = runs_root
        self._config_path = config_path
        self._processed_state_path = processed_state_path
        self._lock = threading.Lock()
        self._tasks: dict[str, TaskState] = {}
        self._active_run_id: str | None = None
        self._stop_events: dict[str, threading.Event] = {}

    def start_task(self, payload: dict[str, object]) -> TaskState | None:
        """启动一个新任务。

        如果当前已有运行中的任务，返回 `None`，由 API 层转成 409。
        """
        request = TaskRequest.from_dict(payload)

        with self._lock:
            active_task = self._tasks.get(self._active_run_id) if self._active_run_id else None
            if active_task is not None and active_task.status not in {"pending", "running", "stopping"}:
                self._active_run_id = None
                active_task = None
            if active_task is not None:
                return None

            run_workspace_service.clear_runs_root(self._runs_root)
            workspace = run_workspace_service.create_run_workspace(
                runs_root=self._runs_root,
                run_id=None,
                run_params={
                    "start_date": request.start_date,
                    "subject_keyword": request.subject_keyword,
                    "force_refresh": request.force_refresh,
                    "max_emails": request.max_emails,
                },
            )

            task = TaskState(
                run_id=workspace.run_id,
                params=request,
                workspace_root=workspace.root_dir,
                mail_output_dir=workspace.mail_output_dir,
                sync_manifest_path=workspace.sync_manifest_path,
            )
            self._tasks[task.run_id] = task
            self._active_run_id = task.run_id
            self._stop_events[task.run_id] = threading.Event()

            worker = threading.Thread(
                target=self._run_task,
                args=(task.run_id,),
                name=f"task-{task.run_id}",
                daemon=True,
            )
            worker.start()
            return copy.deepcopy(task)

    def stop_task(self, run_id: str) -> TaskState | None:
        """请求停止指定任务。"""
        with self._lock:
            task = self._tasks.get(run_id)
            if task is None:
                return None
            if task.status not in {"pending", "running", "stopping"}:
                return copy.deepcopy(task)

            stop_event = self._stop_events.get(run_id)
            if stop_event is not None:
                stop_event.set()
            task.stop_requested = True
            if task.status in {"pending", "running"}:
                task.status = "stopping"
            return copy.deepcopy(task)

    def stop_active_task(self) -> TaskState | None:
        """停止当前活动任务。"""
        with self._lock:
            run_id = self._active_run_id
        if not run_id:
            return None
        return self.stop_task(run_id)

    def get_active_task(self) -> TaskState | None:
        """读取当前活动任务。"""
        with self._lock:
            task = self._tasks.get(self._active_run_id) if self._active_run_id else None
            if task is None:
                return None
            if task.status not in {"pending", "running", "stopping"}:
                self._active_run_id = None
                return None
            return copy.deepcopy(task)

    def get_task(self, run_id: str) -> TaskState | None:
        """按 run_id 读取任务快照。"""
        with self._lock:
            task = self._tasks.get(run_id)
            return copy.deepcopy(task) if task is not None else None

    def _set_item_state(self, run_id: str, item_id: str, *, status: str, reason: str = "") -> None:
        """更新任务中某封邮件的状态。"""
        with self._lock:
            task = self._tasks.get(run_id)
            if task is None:
                return
            for item in task.items:
                if item.item_id == item_id:
                    item.status = status
                    item.reason = reason
                    return

    def _finish_task(self, run_id: str, *, status: str, error_message: str = "") -> None:
        """统一结束任务。"""
        with self._lock:
            task = self._tasks[run_id]
            task.status = status
            task.error_message = error_message
            task.finished_at = utc_now_iso()
            task.stop_requested = False
            if self._active_run_id == run_id:
                self._active_run_id = None
            self._stop_events.pop(run_id, None)

    def _run_task(self, run_id: str) -> None:
        """后台线程主流程。"""
        with self._lock:
            task = self._tasks.get(run_id)
            if task is None:
                return
            task.status = "stopping" if task.stop_requested else "running"
            task.started_at = utc_now_iso()
            request = task.params
            mail_output_dir = task.mail_output_dir
            sync_manifest_path = task.sync_manifest_path
            stop_event = self._stop_events[run_id]

        try:
            processed_message_ids = load_processed_message_ids(self._processed_state_path)
            emails = mail_pipeline_service.fetch_candidate_emails(
                start_date=request.start_date,
                subject_keyword=request.subject_keyword,
                max_emails=request.max_emails,
            )

            with self._lock:
                self._tasks[run_id].items = [
                    TaskItemState(
                        item_id=_item_id(email_data, index),
                        message_id=str(email_data.get("message_id", "")).strip(),
                        subject=str(email_data.get("subject", "")),
                    )
                    for index, email_data in enumerate(emails, start=1)
                ]

            if stop_event.is_set():
                self._finish_task(run_id, status="stopped")
                return

            for index, email_data in enumerate(emails, start=1):
                if stop_event.is_set():
                    self._finish_task(run_id, status="stopped")
                    return

                item_id = _item_id(email_data, index)
                message_id = str(email_data.get("message_id", "")).strip()

                if not request.force_refresh and message_id and message_id in processed_message_ids:
                    self._set_item_state(run_id, item_id, status="skipped", reason="本地已处理过")
                    continue

                self._set_item_state(run_id, item_id, status="analyzing")
                analysis_item = asyncio.run(
                    mail_pipeline_service.analyze_email(
                        email_data=email_data,
                        output_dir=mail_output_dir,
                        force_refresh=request.force_refresh,
                    )
                )

                if analysis_item.analysis_error:
                    self._set_item_state(run_id, item_id, status="failed", reason="邮件分析失败")
                    continue

                self._set_item_state(run_id, item_id, status="syncing")
                try:
                    payload = _read_analysis_payload(analysis_item)
                except Exception:
                    self._set_item_state(run_id, item_id, status="failed", reason="分析结果损坏")
                    continue

                try:
                    sync_item = sync_service.sync_analysis_record(
                        payload,
                        config_path=self._config_path,
                        manifest_path=sync_manifest_path,
                        source_path=analysis_item.output_path,
                    )
                except Exception:
                    self._set_item_state(run_id, item_id, status="failed", reason="同步失败")
                    continue

                if sync_item.status == "synced":
                    self._set_item_state(run_id, item_id, status="synced")
                    mark_message_processed(
                        self._processed_state_path,
                        processed_message_ids,
                        message_id,
                    )
                    continue

                if sync_item.status == "skipped":
                    skip_reason = str(getattr(sync_item, "error", ""))
                    self._set_item_state(
                        run_id,
                        item_id,
                        status="skipped",
                        reason=_display_skip_reason(skip_reason),
                    )
                    if skip_reason.strip().lower() in {"already synced", "already synced in table"}:
                        mark_message_processed(
                            self._processed_state_path,
                            processed_message_ids,
                            message_id,
                        )
                    continue

                self._set_item_state(run_id, item_id, status="failed", reason="同步失败")

                if stop_event.is_set():
                    self._finish_task(run_id, status="stopped")
                    return
        except Exception as exc:
            self._finish_task(run_id, status="failed", error_message=str(exc))
            return

        self._finish_task(run_id, status="completed")
