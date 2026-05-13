"""FastAPI 请求体模型。"""

from pydantic import AliasChoices, BaseModel, Field


class TaskPayload(BaseModel):
    """前端启动单次抽取任务时提交的参数。"""

    interval_seconds: int = Field(default=300, ge=1)
    start_date: str = Field(min_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$")
    subject_keyword: str = "周报"
    force_refresh: bool = False
    max_emails: int | None = Field(
        default=None,
        ge=1,
        validation_alias=AliasChoices("max_emails", "extract_count", "extraction_count", "mail_limit"),
    )
