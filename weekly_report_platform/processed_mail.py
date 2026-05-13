"""本地已处理邮件状态。"""

from __future__ import annotations

import json
from pathlib import Path


def load_processed_message_ids(state_path: Path) -> set[str]:
    """读取本地已成功处理过的 message_id 集合。"""
    if not state_path.exists():
        return set()

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("processed message state must be a JSON array")

    result: set[str] = set()
    for item in payload:
        if isinstance(item, str) and item.strip():
            result.add(item.strip())
    return result


def save_processed_message_ids(state_path: Path, message_ids: set[str]) -> None:
    """把当前集合保存成 JSON 数组。"""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    ordered_ids = sorted(message_id for message_id in message_ids if message_id.strip())
    state_path.write_text(json.dumps(ordered_ids, ensure_ascii=False, indent=2), encoding="utf-8")


def mark_message_processed(state_path: Path, message_ids: set[str], message_id: str) -> bool:
    """把某封邮件标记为已处理。"""
    normalized = message_id.strip()
    if not normalized:
        return False
    if normalized in message_ids:
        return False

    message_ids.add(normalized)
    save_processed_message_ids(state_path, message_ids)
    return True
