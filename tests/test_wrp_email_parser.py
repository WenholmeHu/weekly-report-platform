"""邮件解析器测试。"""

from __future__ import annotations

from email.header import Header
from email.message import EmailMessage
from io import BytesIO
from datetime import datetime, timezone

from openpyxl import Workbook

from weekly_report_platform.domain.mail_analysis import email_parser


def _build_email_service() -> email_parser.EmailService:
    # 绕过 __init__，避免测试时真的去加载环境配置。
    return object.__new__(email_parser.EmailService)


def test_wrp_email_parser_decodes_utf8_subject_header() -> None:
    service = _build_email_service()
    message = EmailMessage()
    message["Subject"] = Header(
        "民生加银基金TA6.0项目周报（交付项目）-20260506-20260510",
        "utf-8",
        header_name="Subject",
    ).encode(linesep="")
    message["From"] = "pm@example.com"
    message["To"] = "team@example.com"
    message.set_content("项目进度正常。", charset="utf-8")

    parsed = service._parse_email(message.as_bytes())

    assert parsed is not None
    assert parsed["subject"] == "民生加银基金TA6.0项目周报（交付项目）-20260506-20260510"


def test_wrp_email_parser_decodes_utf8_body_and_attachment_filename() -> None:
    service = _build_email_service()
    message = EmailMessage()
    message["Subject"] = "项目周报"
    message["From"] = "pm@example.com"
    message["To"] = "team@example.com"
    message.set_content("项目进度正常，风险已跟踪。", charset="utf-8")
    message.add_attachment(
        b"dummy",
        maintype="application",
        subtype="octet-stream",
        filename=("utf-8", "", "民生加银项目周报.xlsx"),
    )

    parsed = service._parse_email(message.as_bytes())

    assert parsed is not None
    assert parsed["text_body"].strip() == "项目进度正常，风险已跟踪。"
    assert parsed["attachments"][0]["filename"] == "民生加银项目周报.xlsx"


def test_wrp_parse_text_attachment_supports_gb18030() -> None:
    content = "关键任务已完成，暂无新增风险。".encode("gb18030")

    parsed = email_parser._parse_text_attachment({"data": content})

    assert parsed == "关键任务已完成，暂无新增风险。"


def test_wrp_parse_excel_attachment_includes_all_sheets() -> None:
    workbook = Workbook()
    sheet1 = workbook.active
    sheet1.title = "进度"
    sheet1.append(["任务", "状态"])
    sheet1.append(["接口联调", "完成"])
    sheet2 = workbook.create_sheet("风险")
    sheet2.append(["风险", "措施"])
    sheet2.append(["网络波动", "增加重试"])

    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()

    parsed = email_parser._parse_excel_attachment({"data": buffer.getvalue()})

    assert "### Sheet: 进度" in parsed
    assert "### Sheet: 风险" in parsed
    assert "接口联调" in parsed
    assert "网络波动" in parsed


def test_wrp_find_emails_by_filter_stops_after_max_emails(monkeypatch) -> None:
    service = _build_email_service()
    service.pop_conn = object()
    service.subject_keyword = "周报"
    loaded_ids: list[int] = []

    monkeypatch.setattr(service, "get_email_count", lambda: 3)
    monkeypatch.setattr(
        service,
        "get_email_header_only",
        lambda message_id: {
            "subject": f"项目周报{message_id}",
            "date": "Wed, 13 May 2026 20:00:00 +0800",
            "message_id": f"<msg-{message_id}>",
        },
    )

    def fake_get_email_by_id(message_id: int):
        loaded_ids.append(message_id)
        return {"id": str(message_id), "subject": f"项目周报{message_id}", "message_id": f"<msg-{message_id}>"}

    monkeypatch.setattr(service, "get_email_by_id", fake_get_email_by_id)

    result = service.find_emails_by_filter(
        start_date=datetime(2026, 5, 1, tzinfo=timezone.utc),
        subject_keyword="周报",
        max_emails=1,
    )

    assert len(result) == 1
    assert loaded_ids == [3]


def test_wrp_find_emails_by_filter_allows_no_start_date(monkeypatch) -> None:
    service = _build_email_service()
    service.pop_conn = object()
    service.subject_keyword = "周报"

    monkeypatch.setattr(service, "get_email_count", lambda: 2)
    monkeypatch.setattr(
        service,
        "get_email_header_only",
        lambda message_id: {
            "subject": f"项目周报{message_id}",
            "date": "",
            "message_id": f"<msg-{message_id}>",
        },
    )
    monkeypatch.setattr(
        service,
        "get_email_by_id",
        lambda message_id: {"id": str(message_id), "subject": f"项目周报{message_id}", "message_id": f"<msg-{message_id}>"},
    )

    result = service.find_emails_by_filter(start_date=None, subject_keyword="", max_emails=None)

    assert [item["id"] for item in result] == ["2", "1"]


# ---- format_email_for_analysis 图片描述测试 ----

def test_wrp_format_email_with_image_descriptions_includes_image_content_section() -> None:
    """image_descriptions 非空时，formatted_content 应包含 [Image Content] 段落。"""
    email_data = {
        "subject": "项目周报",
        "from": "pm@example.com",
        "date": "Mon, 28 Apr 2026 20:00:00 +0800",
        "text_body": "本周进度正常。",
        "html_body": "",
        "attachments": [],
    }
    descriptions = ["[chart.png]: 进度图表，3/5任务完成", "[risk.png]: 风险矩阵截图"]
    result = email_parser.format_email_for_analysis(email_data, image_descriptions=descriptions)
    assert "[Image Content]" in result
    assert "[chart.png]: 进度图表" in result
    assert "[risk.png]: 风险矩阵截图" in result


def test_wrp_format_email_without_image_descriptions_has_no_image_content_section() -> None:
    """image_descriptions 为空或 None 时，formatted_content 不包含 [Image Content] 段落。"""
    email_data = {
        "subject": "项目周报",
        "from": "pm@example.com",
        "date": "Mon, 28 Apr 2026 20:00:00 +0800",
        "text_body": "本周进度正常。",
        "html_body": "",
        "attachments": [],
    }
    result_none = email_parser.format_email_for_analysis(email_data, image_descriptions=None)
    assert "[Image Content]" not in result_none

    result_empty = email_parser.format_email_for_analysis(email_data, image_descriptions=[])
    assert "[Image Content]" not in result_empty

    result_default = email_parser.format_email_for_analysis(email_data)
    assert "[Image Content]" not in result_default
