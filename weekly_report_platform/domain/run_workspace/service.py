"""run 工作目录服务。"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True)
class RunWorkspace:
    """一次任务对应的目录快照。

    字段说明：
    - `run_id`：本次任务的唯一编号，也是 `runs/<run_id>/` 的目录名。
    - `runs_root`：所有 run 的根目录，通常是项目下的 `runs/`。
    - `root_dir`：当前 run 的根目录。
    - `mail_output_dir`：邮件分析结果输出目录。
    - `sync_dir`：同步阶段的工作目录。
    - `sync_manifest_path`：同步清单文件路径。
    - `logs_dir`：当前 run 的日志目录。
    - `run_context_path`：本次运行上下文文件。
    """

    run_id: str
    runs_root: Path
    root_dir: Path
    mail_output_dir: Path
    sync_dir: Path
    sync_manifest_path: Path
    logs_dir: Path
    run_context_path: Path


def build_run_id(now: datetime | None = None) -> str:
    """生成目录安全的 run_id。"""
    current = now or datetime.now(timezone.utc)
    return f"run_{current.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"


def clear_runs_root(runs_root: Path) -> Path:
    """清空 runs 根目录。

    当前项目只保留最近一轮执行产物，所以新任务开始前会先删掉旧 run。
    """
    resolved_runs_root = runs_root.resolve()
    resolved_runs_root.mkdir(parents=True, exist_ok=True)

    for child in resolved_runs_root.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
            continue
        child.unlink()

    return resolved_runs_root


def create_run_workspace(
    runs_root: Path,
    run_id: str | None,
    run_params: dict[str, object],
) -> RunWorkspace:
    """创建一次执行需要的目录结构。"""
    resolved_runs_root = runs_root.resolve()
    resolved_runs_root.mkdir(parents=True, exist_ok=True)

    effective_run_id = run_id or build_run_id()
    root_dir = resolved_runs_root / effective_run_id
    mail_output_dir = root_dir / "mail" / "risk_json"
    sync_dir = root_dir / "sync"
    logs_dir = root_dir / "logs"
    run_context_path = root_dir / "run_context.json"
    sync_manifest_path = sync_dir / "sync_manifest.json"

    mail_output_dir.mkdir(parents=True, exist_ok=False)
    sync_dir.mkdir(parents=True, exist_ok=False)
    logs_dir.mkdir(parents=True, exist_ok=False)

    workspace = RunWorkspace(
        run_id=effective_run_id,
        runs_root=resolved_runs_root,
        root_dir=root_dir,
        mail_output_dir=mail_output_dir,
        sync_dir=sync_dir,
        sync_manifest_path=sync_manifest_path,
        logs_dir=logs_dir,
        run_context_path=run_context_path,
    )
    write_run_context(workspace=workspace, run_params=run_params)
    return workspace


def write_run_context(workspace: RunWorkspace, run_params: dict[str, object]) -> None:
    """把本次执行参数和关键路径写入 `run_context.json`。"""
    payload = {
        "run_id": workspace.run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "runs_root": str(workspace.runs_root),
        "workspace_root": str(workspace.root_dir),
        "mail_output_dir": str(workspace.mail_output_dir),
        "sync_manifest_path": str(workspace.sync_manifest_path),
        "params": {
            "start_date": run_params.get("start_date"),
            "subject_keyword": run_params.get("subject_keyword"),
            "force_refresh": run_params.get("force_refresh", False),
        },
    }
    workspace.run_context_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
