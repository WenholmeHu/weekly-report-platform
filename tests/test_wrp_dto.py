"""运行时请求模型解析测试。"""

from weekly_report_platform.runtime import TaskRequest


def test_wrp_task_request_force_refresh_always_true() -> None:
    request = TaskRequest.from_dict(
        {
            "start_date": "2026-05-01",
            "subject_keyword": "周报",
        }
    )

    assert request.force_refresh is True


def test_wrp_task_request_from_dict_parses_optional_max_emails() -> None:
    request = TaskRequest.from_dict(
        {
            "start_date": "2026-05-01",
            "subject_keyword": "周报",
            "max_emails": "1",
        }
    )

    assert request.max_emails == 1


def test_wrp_task_request_from_dict_treats_empty_max_emails_as_unlimited() -> None:
    request = TaskRequest.from_dict(
        {
            "start_date": "2026-05-01",
            "subject_keyword": "周报",
            "max_emails": "",
        }
    )

    assert request.max_emails is None


def test_wrp_task_request_from_dict_treats_empty_start_date_as_unlimited_history() -> None:
    request = TaskRequest.from_dict(
        {
            "start_date": "",
            "subject_keyword": "",
            "max_emails": "",
        }
    )

    assert request.start_date is None
    assert request.subject_keyword == "周报"
    assert request.max_emails is None