"""邮件分析服务测试。"""

import asyncio
import json

from weekly_report_platform.domain.mail_analysis import service as pipeline_service


def test_wrp_fetch_candidate_emails_logs_in_filters_and_logs_out(monkeypatch) -> None:
    email_data = [
        {"id": "1", "subject": "周报A", "message_id": "<msg-1>"},
        {"id": "2", "subject": "周报B", "message_id": "<msg-2>"},
    ]
    observed: dict[str, object] = {}

    monkeypatch.setattr(pipeline_service.email_service, "login_pop3", lambda: True)

    def fake_find(start_date, subject_keyword, max_emails=None):
        observed["subject_keyword"] = subject_keyword
        observed["max_emails"] = max_emails
        return email_data

    monkeypatch.setattr(pipeline_service.email_service, "find_emails_by_filter", fake_find)
    monkeypatch.setattr(pipeline_service.email_service, "logout", lambda: observed.setdefault("logged_out", True))

    result = pipeline_service.fetch_candidate_emails(
        start_date="2026-04-27",
        subject_keyword="周报",
    )

    assert len(result) == 2
    assert result[0]["message_id"] == "<msg-1>"
    assert result[1]["message_id"] == "<msg-2>"
    assert observed["subject_keyword"] == "周报"
    assert observed["max_emails"] is None
    assert observed["logged_out"] is True


def test_wrp_fetch_candidate_emails_passes_optional_max_emails(monkeypatch) -> None:
    observed: dict[str, object] = {}

    monkeypatch.setattr(pipeline_service.email_service, "login_pop3", lambda: True)

    def fake_find(start_date, subject_keyword, max_emails=None):
        observed["subject_keyword"] = subject_keyword
        observed["max_emails"] = max_emails
        return [{"id": "1", "subject": "周报A", "message_id": "<msg-1>"}]

    monkeypatch.setattr(pipeline_service.email_service, "find_emails_by_filter", fake_find)
    monkeypatch.setattr(pipeline_service.email_service, "logout", lambda: None)

    result = pipeline_service.fetch_candidate_emails(
        start_date="2026-04-27",
        subject_keyword="周报",
        max_emails=1,
    )

    assert len(result) == 1
    assert observed["subject_keyword"] == "周报"
    assert observed["max_emails"] == 1


def test_wrp_fetch_candidate_emails_allows_empty_start_date(monkeypatch) -> None:
    observed: dict[str, object] = {}

    monkeypatch.setattr(pipeline_service.email_service, "login_pop3", lambda: True)

    def fake_find(start_date, subject_keyword, max_emails=None):
        observed["start_date"] = start_date
        observed["subject_keyword"] = subject_keyword
        observed["max_emails"] = max_emails
        return [{"id": "1", "subject": "周报A", "message_id": "<msg-1>"}]

    monkeypatch.setattr(pipeline_service.email_service, "find_emails_by_filter", fake_find)
    monkeypatch.setattr(pipeline_service.email_service, "logout", lambda: None)

    result = pipeline_service.fetch_candidate_emails(
        start_date=None,
        subject_keyword="",
        max_emails=None,
    )

    assert len(result) == 1
    assert observed["start_date"] is None
    assert observed["subject_keyword"] == "周报"
    assert observed["max_emails"] is None


def test_wrp_analyze_email_returns_payload_and_writes_output_file(monkeypatch, workspace_tmp_path) -> None:
    email_data = {
        "id": "1",
        "message_id": "<msg-1>",
        "subject": "周报A",
        "from": "a@example.com",
        "to": "b@example.com",
        "cc": "",
        "date": "Mon, 28 Apr 2026 20:00:00 +0800",
        "text_body": "body",
        "html_body": "",
        "attachments": [],
    }

    monkeypatch.setattr(pipeline_service, "format_email_for_analysis", lambda _: "formatted content")

    async def fake_process(_: str, __: str):
        return {
            "进度抽取结果": {
                "分析结果": {
                    "综合总结": "总结A"
                }
            }
        }, []

    monkeypatch.setattr(pipeline_service, "process_email_analysis_with_errors", fake_process)

    result = asyncio.run(
        pipeline_service.analyze_email(
            email_data=email_data,
            output_dir=workspace_tmp_path / "runs" / "run_1" / "mail" / "risk_json",
            force_refresh=False,
        )
    )

    assert result.analysis_error == ""
    assert result.reused_existing is False
    saved_payload = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert saved_payload["analysis"]["进度抽取结果"]["分析结果"]["综合总结"] == "总结A"


def test_wrp_analyze_email_marks_llm_extraction_errors_as_analysis_error(monkeypatch, workspace_tmp_path) -> None:
    email_data = {
        "id": "1",
        "message_id": "<msg-1>",
        "subject": "周报A",
        "from": "a@example.com",
        "to": "b@example.com",
        "cc": "",
        "date": "Mon, 28 Apr 2026 20:00:00 +0800",
        "text_body": "body",
        "html_body": "",
        "attachments": [],
    }

    monkeypatch.setattr(pipeline_service, "format_email_for_analysis", lambda _: "formatted content")

    async def fake_process(_: str, __: str):
        return {}, ["Progress extraction failed: Request error calling LLM API: All connection attempts failed"]

    monkeypatch.setattr(pipeline_service, "process_email_analysis_with_errors", fake_process)

    result = asyncio.run(
        pipeline_service.analyze_email(
            email_data=email_data,
            output_dir=workspace_tmp_path / "runs" / "run_1" / "mail" / "risk_json",
            force_refresh=False,
        )
    )

    assert "Progress extraction failed" in result.analysis_error
    saved_payload = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert "All connection attempts failed" in saved_payload["errors"]
