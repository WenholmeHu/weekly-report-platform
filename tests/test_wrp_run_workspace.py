"""run 工作区测试。"""

import json
import re
from pathlib import Path

from weekly_report_platform.domain.run_workspace.service import build_run_id, clear_runs_root, create_run_workspace


def test_wrp_build_run_id_returns_directory_safe_value() -> None:
    run_id = build_run_id()
    assert run_id
    assert re.fullmatch(r"[A-Za-z0-9_-]+", run_id)


def test_wrp_create_run_workspace_creates_expected_layout(workspace_tmp_path: Path) -> None:
    workspace = create_run_workspace(
        runs_root=workspace_tmp_path / "runs",
        run_id="run_20260429_120000_abcd1234",
        run_params={
            "start_date": "2026-04-29",
            "subject_keyword": "周报",
            "force_refresh": True,
        },
    )

    assert workspace.root_dir.is_dir()
    assert workspace.mail_output_dir.is_dir()
    assert workspace.sync_dir.is_dir()
    assert workspace.logs_dir.is_dir()
    payload = json.loads(workspace.run_context_path.read_text(encoding="utf-8"))
    assert payload["params"]["subject_keyword"] == "周报"
    assert payload["params"]["force_refresh"] is True


def test_wrp_clear_runs_root_removes_existing_children(workspace_tmp_path: Path) -> None:
    runs_root = workspace_tmp_path / "runs"
    old_run_dir = runs_root / "run_old_1" / "mail" / "risk_json"
    old_run_dir.mkdir(parents=True, exist_ok=True)
    (old_run_dir / "result.json").write_text("{}", encoding="utf-8")
    (runs_root / "stale.txt").write_text("stale", encoding="utf-8")

    returned_root = clear_runs_root(runs_root)

    assert returned_root == runs_root.resolve()
    assert list(runs_root.iterdir()) == []
