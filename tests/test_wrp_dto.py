"""运行时请求模型解析测试。"""

from weekly_report_platform.runtime import TaskRequest


def test_wrp_task_request_from_dict_parses_force_refresh_string_false() -> None:
    # 防止字符串 "false" 被错误解析成 True。
    request = TaskRequest.from_dict(
        {
            "start_date": "2026-05-01",
            "subject_keyword": "周报",
            "force_refresh": "false",
        }
    )

    assert request.force_refresh is False


def test_wrp_task_request_from_dict_parses_force_refresh_string_true() -> None:
    # 页面 checkbox/脚本输入常见的 "on" 也应被识别为 True。
    request = TaskRequest.from_dict(
        {
            "start_date": "2026-05-01",
            "subject_keyword": "周报",
            "force_refresh": "on",
        }
    )

    assert request.force_refresh is True


def test_wrp_task_request_from_dict_parses_optional_max_emails() -> None:
    request = TaskRequest.from_dict(
        {
            "start_date": "2026-05-01",
            "subject_keyword": "周报",
            "force_refresh": False,
            "max_emails": "1",
        }
    )

    assert request.max_emails == 1


def test_wrp_task_request_from_dict_treats_empty_max_emails_as_unlimited() -> None:
    request = TaskRequest.from_dict(
        {
            "start_date": "2026-05-01",
            "subject_keyword": "周报",
            "force_refresh": False,
            "max_emails": "",
        }
    )

    assert request.max_emails is None


def test_wrp_task_request_from_dict_treats_empty_start_date_as_unlimited_history() -> None:
    request = TaskRequest.from_dict(
        {
            "start_date": "",
            "subject_keyword": "",
            "force_refresh": False,
            "max_emails": "",
        }
    )

    assert request.start_date is None
    assert request.subject_keyword == "周报"
    assert request.max_emails is None
