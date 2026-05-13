"""FastAPI 入口。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from weekly_report_platform.infrastructure.config import (
    DEFAULT_DINGTALK_CONFIG_PATH,
    DEFAULT_PROCESSED_STATE_PATH,
)
from weekly_report_platform.runtime import TaskManager


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = PROJECT_ROOT / "runs"
CONFIG_PATH = DEFAULT_DINGTALK_CONFIG_PATH
PROCESSED_STATE_PATH = DEFAULT_PROCESSED_STATE_PATH
STATIC_DIR = PROJECT_ROOT / "weekly_report_platform" / "web" / "static"

task_manager = TaskManager(
    runs_root=RUNS_ROOT,
    config_path=CONFIG_PATH,
    processed_state_path=PROCESSED_STATE_PATH,
)


def _conflict_response(message: str) -> JSONResponse:
    """统一返回任务冲突响应。"""
    active_task = task_manager.get_active_task()
    return JSONResponse(
        status_code=409,
        content={
            "error": message,
            "active_task": active_task.to_dict() if active_task is not None else None,
        },
    )


def _build_task_payload(payload: dict[str, object] | None = None) -> dict[str, object]:
    """构造单次任务参数；缺省值只来自当前请求规则。"""
    payload = payload or {}
    return {
        "start_date": payload.get("start_date"),
        "subject_keyword": payload.get("subject_keyword", "周报"),
        "force_refresh": payload.get("force_refresh", False),
        "max_emails": payload.get("max_emails"),
    }


def create_app() -> FastAPI:
    """创建 FastAPI 应用，并注册页面和 API。"""
    app = FastAPI(title="weekly-report-platform backend", version="0.1.0")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def frontend_home() -> FileResponse:
        """返回前端首页。"""
        return FileResponse(STATIC_DIR / "index.html")

    @app.post("/api/tasks", status_code=202, response_model=None)
    def create_task(payload: dict[str, object] | None = Body(default=None)):
        """手动启动一轮任务。

        参数均为本次任务参数：
        - `start_date` 为空时扫描邮箱历史。
        - `subject_keyword` 为空时默认使用“周报”。
        - `max_emails` 为空时不限制数量。
        """
        task = task_manager.start_task(_build_task_payload(payload))
        if task is None:
            return _conflict_response("another task is already running")
        return {"run_id": task.run_id, "status": task.status}

    @app.post("/api/tasks/stop")
    def stop_active_task() -> dict[str, object]:
        """请求停止当前正在执行的任务。"""
        task = task_manager.stop_active_task()
        if task is None:
            raise HTTPException(status_code=404, detail="no active task")
        return task.to_dict()

    @app.get("/api/tasks/{run_id}")
    def get_task(run_id: str) -> dict[str, object]:
        """读取指定 run 的完整状态。"""
        task = task_manager.get_task(run_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return task.to_dict()

    return app


def main() -> None:
    import uvicorn

    config = uvicorn.Config(
        "weekly_report_platform.api.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=8000,
    )
    server = uvicorn.Server(config)

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.serve())
    finally:
        asyncio.set_event_loop(None)
        loop.close()


if __name__ == "__main__":
    main()
