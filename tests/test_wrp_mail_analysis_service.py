"""邮件分析服务测试。"""

import asyncio
import json

from weekly_report_platform.domain.mail_analysis import service as pipeline_service
from weekly_report_platform.domain.mail_analysis.image_analyzer import ImageAnalysisResult
from weekly_report_platform.domain.mail_analysis.image_filter import ValidImage


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

    monkeypatch.setattr(pipeline_service, "format_email_for_analysis", lambda _email_data, *, image_descriptions=None: "formatted content")

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

    monkeypatch.setattr(pipeline_service, "format_email_for_analysis", lambda _email_data, *, image_descriptions=None: "formatted content")

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


def test_wrp_run_mail_risk_pipeline_returns_summary_and_items(monkeypatch) -> None:
    email_data = [
        {"id": "1", "message_id": "<msg-1>", "subject": "周报A"},
        {"id": "2", "message_id": "<msg-2>", "subject": "周报B"},
    ]
    monkeypatch.setattr(pipeline_service, "fetch_candidate_emails", lambda **_: email_data)

    results = [
        pipeline_service.MailAnalysisItemResult(
            message_id="<msg-1>",
            subject="周报A",
            payload={"message_id": "<msg-1>"},
            output_path=None,
        ),
        pipeline_service.MailAnalysisItemResult(
            message_id="<msg-2>",
            subject="周报B",
            payload={"message_id": "<msg-2>", "errors": "boom"},
            output_path=None,
            analysis_error="boom",
        ),
    ]

    async def fake_analyze_email(*, email_data, output_dir, force_refresh):
        return results.pop(0)

    monkeypatch.setattr(pipeline_service, "analyze_email", fake_analyze_email)

    result = asyncio.run(
        pipeline_service.run_mail_risk_pipeline(
            pipeline_service.MailRiskPipelineRequest(
                start_date="2026-04-27",
                subject_keyword="周报",
                output_dir=None,
                force_refresh=False,
            )
        )
    )

    assert result.total == 2
    assert result.processed == 1
    assert result.failed == 1


# ---- 图片分析集成测试 ----

def test_wrp_analyze_email_injects_image_descriptions_into_formatted_content(monkeypatch, workspace_tmp_path) -> None:
    """带图片的邮件分析：image_descriptions 应注入 formatted_content。"""
    email_data = {
        "id": "1",
        "message_id": "<msg-img>",
        "subject": "周报含图",
        "from": "a@example.com",
        "to": "b@example.com",
        "cc": "",
        "date": "Mon, 28 Apr 2026 20:00:00 +0800",
        "text_body": "body",
        "html_body": "",
        "attachments": [],
    }

    # mock 提取和过滤，返回一张有效图片
    valid_img = ValidImage(data=b"img", content_type="image/png", filename="chart.png", size=1000)
    monkeypatch.setattr(pipeline_service, "extract_images_from_email", lambda _: [1])  # 返回非空以触发分析
    monkeypatch.setattr(pipeline_service, "filter_images", lambda _, config=None: [valid_img])

    captured_descriptions = None

    def capturing_format(_email_data, *, image_descriptions=None):
        nonlocal captured_descriptions
        captured_descriptions = image_descriptions
        return "formatted content"

    monkeypatch.setattr(pipeline_service, "format_email_for_analysis", capturing_format)

    async def fake_analyze_images(images):
        return [ImageAnalysisResult(filename="chart.png", description="进度图表，3/5任务完成")]

    monkeypatch.setattr(pipeline_service, "analyze_images", fake_analyze_images)

    async def fake_process(_: str, __: str):
        return {"进度抽取结果": {"分析结果": {"综合总结": "含图总结"}}}, []

    monkeypatch.setattr(pipeline_service, "process_email_analysis_with_errors", fake_process)

    result = asyncio.run(
        pipeline_service.analyze_email(
            email_data=email_data,
            output_dir=workspace_tmp_path / "runs" / "run_2" / "mail" / "risk_json",
            force_refresh=True,
        )
    )
    assert result.analysis_error == ""
    assert captured_descriptions is not None
    assert len(captured_descriptions) == 1
    assert "[chart.png]" in captured_descriptions[0]


def test_wrp_analyze_email_image_analysis_failure_does_not_block(monkeypatch, workspace_tmp_path) -> None:
    """图片分析失败不应阻塞文本分析，错误信息应出现在 analysis_error 中。"""
    email_data = {
        "id": "1",
        "message_id": "<msg-imgfail>",
        "subject": "周报图分析失败",
        "from": "a@example.com",
        "to": "b@example.com",
        "cc": "",
        "date": "Mon, 28 Apr 2026 20:00:00 +0800",
        "text_body": "body",
        "html_body": "",
        "attachments": [],
    }

    # mock 提取和过滤，返回一张图片，让后续 analyze_images 抛异常
    valid_img = ValidImage(data=b"img", content_type="image/png", filename="chart.png", size=1000)
    monkeypatch.setattr(pipeline_service, "extract_images_from_email", lambda _: [1])
    monkeypatch.setattr(pipeline_service, "filter_images", lambda _, config=None: [valid_img])

    monkeypatch.setattr(pipeline_service, "format_email_for_analysis", lambda _ed, *, image_descriptions=None: "formatted content")

    async def fake_analyze_images(images):
        raise RuntimeError("图片分析服务不可用")

    monkeypatch.setattr(pipeline_service, "analyze_images", fake_analyze_images)

    async def fake_process(_: str, __: str):
        return {"进度抽取结果": {"分析结果": {"综合总结": "正常总结"}}}, []

    monkeypatch.setattr(pipeline_service, "process_email_analysis_with_errors", fake_process)

    result = asyncio.run(
        pipeline_service.analyze_email(
            email_data=email_data,
            output_dir=workspace_tmp_path / "runs" / "run_3" / "mail" / "risk_json",
            force_refresh=True,
        )
    )
    # 文本分析正常完成
    assert result.payload is not None
    assert result.payload["analysis"]["进度抽取结果"]["分析结果"]["综合总结"] == "正常总结"
    # 图片分析错误记录到 analysis_error
    assert "image analysis failed" in result.analysis_error


def test_wrp_analyze_email_no_images_no_image_content_section(monkeypatch, workspace_tmp_path) -> None:
    """不带图片的邮件：formatted_content 不包含 [Image Content] 段落。"""
    email_data = {
        "id": "1",
        "message_id": "<msg-noimg>",
        "subject": "周报无图",
        "from": "a@example.com",
        "to": "b@example.com",
        "cc": "",
        "date": "Mon, 28 Apr 2026 20:00:00 +0800",
        "text_body": "纯文本周报",
        "html_body": "",
        "attachments": [],
    }

    # 无图片时提取和过滤返回空
    monkeypatch.setattr(pipeline_service, "extract_images_from_email", lambda _: [])
    monkeypatch.setattr(pipeline_service, "filter_images", lambda _, config=None: [])

    captured_descriptions = None

    def capturing_format(_email_data, *, image_descriptions=None):
        nonlocal captured_descriptions
        captured_descriptions = image_descriptions
        return "formatted content"

    monkeypatch.setattr(pipeline_service, "format_email_for_analysis", capturing_format)

    async def fake_process(_: str, __: str):
        return {"进度抽取结果": {"分析结果": {"综合总结": "无图总结"}}}, []

    monkeypatch.setattr(pipeline_service, "process_email_analysis_with_errors", fake_process)

    result = asyncio.run(
        pipeline_service.analyze_email(
            email_data=email_data,
            output_dir=workspace_tmp_path / "runs" / "run_4" / "mail" / "risk_json",
            force_refresh=True,
        )
    )
    assert result.analysis_error == ""
    assert captured_descriptions == []
