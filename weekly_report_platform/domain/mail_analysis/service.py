"""邮件分析服务。

职责很直接：
- 从邮箱里拉出候选邮件。
- 把单封邮件整理成模型输入并调用分析。
- 把分析结果落成 JSON artifact，供后续同步到钉钉。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from weekly_report_platform.domain.mail_analysis.email_parser import email_service, format_email_for_analysis
from weekly_report_platform.domain.mail_analysis.prompts import process_email_analysis_with_errors
from weekly_report_platform.infrastructure.logging import logger


@dataclass(frozen=True)
class MailRiskPipelineRequest:
    """批量邮件分析阶段的输入参数。"""

    start_date: str | None
    subject_keyword: str = "周报"
    output_dir: Path | None = None
    force_refresh: bool = False
    max_emails: int | None = None


@dataclass(frozen=True)
class MailAnalysisItemResult:
    """单封邮件分析结果。

    字段说明：
    - `message_id`: 邮件唯一标识。
    - `subject`: 邮件主题。
    - `payload`: 分析结果 JSON；如果复用了已有 artifact，这里可以为空。
    - `output_path`: artifact 文件路径。
    - `analysis_error`: 分析阶段错误摘要。
    - `reused_existing`: 是否直接复用了已有 artifact。
    """

    message_id: str
    subject: str
    payload: dict[str, Any] | None
    output_path: Path | None
    analysis_error: str = ""
    reused_existing: bool = False


@dataclass(frozen=True)
class MailRiskPipelineResult:
    """一批邮件分析的汇总结果。"""

    total: int
    processed: int
    skipped: int
    failed: int
    items: list[MailAnalysisItemResult]


def _sanitize_filename(name: str) -> str:
    """清洗出适合 Windows 落盘的文件名。"""
    cleaned = re.sub(r"[\\/:*?\"<>|]", "_", name or "")
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._")
    return cleaned[:160] if cleaned else "unknown"


def build_output_payload(email_data: dict[str, Any], analysis: dict[str, Any], error: str = "") -> dict[str, Any]:
    """构造单封邮件的分析 artifact。

    返回值：
    - 一个 dict，后续会直接写成 UTF-8 JSON 文件并同步到钉钉。
    """
    attachments = email_data.get("attachments", [])
    return {
        "message_id": email_data.get("message_id", "").strip(),
        "subject": email_data.get("subject", ""),
        "from": email_data.get("from", ""),
        "to": email_data.get("to", ""),
        "cc": email_data.get("cc", ""),
        "date": email_data.get("date", ""),
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "has_text_body": bool(email_data.get("text_body", "").strip()),
            "has_html_body": bool(email_data.get("html_body", "").strip()),
            "attachment_names": [attachment.get("filename", "") for attachment in attachments],
            "attachment_count": len(attachments),
        },
        "analysis": analysis,
        "errors": error,
    }


def resolve_output_path(output_dir: Path, email_data: dict[str, Any]) -> Path:
    """为当前邮件计算 artifact 输出路径。"""
    message_id = str(email_data.get("message_id") or "").strip()
    if message_id:
        base = _sanitize_filename(message_id)
    else:
        subject = _sanitize_filename(str(email_data.get("subject", "no_subject")))
        fallback_id = _sanitize_filename(str(email_data.get("id", "no_id")))
        base = f"{subject}_{fallback_id}"
    return output_dir / f"{base}.json"


def write_analysis_artifact(output_path: Path, payload: dict[str, Any]) -> None:
    """把分析结果写到磁盘。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def validate_output_dir(output_dir: Path | None) -> None:
    """防止占位目录原样传入。"""
    if output_dir is None:
        return

    output_dir_text = str(output_dir)
    if "<run_id>" in output_dir_text:
        raise ValueError(
            "output_dir still contains the placeholder '<run_id>'; replace <run_id> with a real run id, "
            "for example 'run_20260429_162500_demo'."
        )


def fetch_candidate_emails(
    start_date: str | None,
    subject_keyword: str,
    max_emails: int | None = None,
) -> list[dict[str, Any]]:
    """登录邮箱并拉取候选邮件。

    返回值：
    - 一个列表，每个元素都是原始邮件 dict，后续会逐封进入分析。
    """
    start_date_value = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) if start_date else None
    keyword = subject_keyword.strip() or "周报"

    if not email_service.login_pop3():
        raise RuntimeError("Unable to connect to POP3 server; please check mailbox settings")

    try:
        emails = email_service.find_emails_by_filter(
            start_date=start_date_value,
            subject_keyword=keyword,
            max_emails=max_emails,
        )
    finally:
        email_service.logout()

    logger.info(f"Candidate emails to process: {len(emails)}")
    return emails


async def analyze_email(
    *,
    email_data: dict[str, Any],
    output_dir: Path | None,
    force_refresh: bool,
) -> MailAnalysisItemResult:
    """分析单封邮件，并尽量落一份 artifact。"""
    output_path = resolve_output_path(output_dir, email_data) if output_dir is not None else None
    if output_path is not None and output_path.exists() and not force_refresh:
        logger.info(f"Skipping existing analysis artifact: {output_path}")
        return MailAnalysisItemResult(
            message_id=str(email_data.get("message_id", "")).strip(),
            subject=str(email_data.get("subject", "")),
            payload=None,
            output_path=output_path,
            reused_existing=True,
        )

    try:
        formatted_content = format_email_for_analysis(email_data)
        analysis, errors = await process_email_analysis_with_errors(
            formatted_content,
            str(email_data.get("subject", "")),
        )
        error_text = "; ".join(errors)
        payload = build_output_payload(email_data, analysis, error=error_text)
        if output_path is not None:
            try:
                write_analysis_artifact(output_path, payload)
            except Exception as exc:
                logger.error(f"Failed to write analysis artifact: {exc}")
                failed_payload = build_output_payload(email_data, analysis={}, error=str(exc))
                return MailAnalysisItemResult(
                    message_id=str(failed_payload.get("message_id", "")).strip(),
                    subject=str(failed_payload.get("subject", "")),
                    payload=failed_payload,
                    output_path=output_path,
                    analysis_error=str(exc),
                )
            logger.info(f"Wrote analysis artifact: {output_path}")
        return MailAnalysisItemResult(
            message_id=str(payload.get("message_id", "")).strip(),
            subject=str(payload.get("subject", "")),
            payload=payload,
            output_path=output_path,
            analysis_error=error_text,
        )
    except Exception as exc:
        logger.error(f"Failed to process email: {exc}")
        payload = build_output_payload(email_data, analysis={}, error=str(exc))
        if output_path is not None:
            try:
                write_analysis_artifact(output_path, payload)
            except Exception as write_exc:
                logger.error(f"Failed to write failure analysis artifact: {write_exc}")
                return MailAnalysisItemResult(
                    message_id=str(payload.get("message_id", "")).strip(),
                    subject=str(payload.get("subject", "")),
                    payload=payload,
                    output_path=output_path,
                    analysis_error=f"{exc}; artifact write failed: {write_exc}",
                )
        return MailAnalysisItemResult(
            message_id=str(payload.get("message_id", "")).strip(),
            subject=str(payload.get("subject", "")),
            payload=payload,
            output_path=output_path,
            analysis_error=str(exc),
        )


async def run_mail_risk_pipeline(request: MailRiskPipelineRequest) -> MailRiskPipelineResult:
    """执行“抓邮件 -> 分析 -> 落 artifact”这一段流程。"""
    validate_output_dir(request.output_dir)

    emails = fetch_candidate_emails(
        start_date=request.start_date,
        subject_keyword=request.subject_keyword,
        max_emails=request.max_emails,
    )

    processed = 0
    skipped = 0
    failed = 0
    items: list[MailAnalysisItemResult] = []

    for email_data in emails:
        item = await analyze_email(
            email_data=email_data,
            output_dir=request.output_dir,
            force_refresh=request.force_refresh,
        )
        items.append(item)
        if item.analysis_error:
            failed += 1
        elif item.reused_existing:
            skipped += 1
        else:
            processed += 1

    logger.info(f"Mail risk pipeline completed: processed={processed}, skipped={skipped}, failed={failed}")
    return MailRiskPipelineResult(
        total=len(emails),
        processed=processed,
        skipped=skipped,
        failed=failed,
        items=items,
    )
