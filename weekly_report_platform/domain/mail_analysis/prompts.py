"""周报分析提示词与结果编排。"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from weekly_report_platform.domain.mail_analysis.llm_client import llm_client
from weekly_report_platform.infrastructure.logging import logger


PROGRESS_KEY = "进度抽取结果"
RISK_KEY = "风险抽取结果"
CONSISTENCY_KEY = "一致性分析"
RAW_EXTRACT_KEY = "原文抽取"
RISK_DETAIL_KEY = "风险详情"
SOURCE_KEY = "来源"
CONTENT_KEY = "原文内容"
RISK_DESC_KEY = "风险描述"
RISK_TYPE_KEY = "风险类型"
RISK_ACTION_KEY = "风险措施和最新进展"
RISK_OWNER_KEY = "风险责任人"
SEQ_KEY = "序号"
PROJECT_NAME_KEY = "项目名称"
PROJECT_CODE_KEY = "项目编号"
PROJECT_CYCLE_KEY = "项目周报周期"
PROJECT_PROGRESS_KEY = "项目进度"
MILESTONE_KEY = "里程碑"
WEEKLY_SUMMARY_KEY = "本周小结"
NEXT_WEEK_PLAN_KEY = "下周计划"
RISK_LEVEL_KEY = "风险等级"
RISK_DUE_DATE_KEY = "计划解决日期"


def clean_json_response(response: str) -> str:
    """去掉模型偶尔附带的 Markdown 代码块外壳。"""
    if not response:
        return ""
    cleaned_response = response.strip()
    if cleaned_response.startswith("```json"):
        cleaned_response = cleaned_response[7:]
    if cleaned_response.startswith("```"):
        cleaned_response = cleaned_response[3:]
    if cleaned_response.endswith("```"):
        cleaned_response = cleaned_response[:-3]
    return cleaned_response.strip()


def _extract_first_json_object(text: str) -> str:
    """从文本中用大括号匹配提取第一个完整 JSON 对象。"""
    first_brace = text.find("{")
    if first_brace == -1:
        return text
    depth = 0
    for i in range(first_brace, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[first_brace : i + 1]
    return text


def _load_json_object_response(response: str, stage_name: str) -> dict[str, Any]:
    """把模型输出解析成 JSON object。"""
    try:
        parsed = json.loads(clean_json_response(response))
    except json.JSONDecodeError as exc:
        # LLM 可能在 JSON 对象后附带解释文字，导致 "Extra data" 错误。
        # 用大括号匹配提取第一个完整 JSON 对象再试。
        cleaned = clean_json_response(response)
        parsed = json.loads(_extract_first_json_object(cleaned))
    if not isinstance(parsed, dict):
        raise ValueError(f"{stage_name} response must be a JSON object")
    return parsed


def _load_json_array_response(response: str, stage_name: str) -> list[dict[str, Any]]:
    """把模型输出解析成 JSON object 数组。"""
    parsed = json.loads(clean_json_response(response))
    if not isinstance(parsed, list):
        raise ValueError(f"{stage_name} response must be a JSON array")

    normalized_items: list[dict[str, Any]] = []
    for index, item in enumerate(parsed, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"{stage_name} item #{index} must be a JSON object")
        normalized_items.append(item)
    return normalized_items


def get_progress_extract_prompt(formatted_full_text: str) -> str:
    """生成进度抽取提示词。"""
    return f"""
You are extracting weekly report progress information from email content and attachments.
Return strict JSON with the exact keys below.

Rules:
- Preserve important source text excerpts when possible.
- If only one source exists, set consistency analysis to "Not applicable".
- Focus on summary/progress sections only.
- For "项目进度", extract a number between 0 and 1 representing the project completion percentage.
  Look for progress indicators like "项目PC", "项目进度" in the email body or attachments.
  Convert percentage values (e.g., "75%" -> 0.75) to decimal form.
- For other fields, extract from the email content and attachments as appropriate.
- For "项目整体进展", locate the "项目整体进展概况" field in the Excel weekly report attachment (typically in the "项目周报" sheet). Extract the original text faithfully — do NOT invent or summarize beyond what is written. Preserve paragraph/line separation for readability. If no such field exists, write a brief summary based on the email body/attachments.
- For "本周小结", locate the "本周小结" or "本周进展小结" field in the Excel weekly report attachment (typically in the "项目周报" sheet). Extract the original text faithfully — do NOT invent, rewrite, or summarize. Preserve line separation for readability. Within each item, replace multiple consecutive spaces with a single comma for readability in table cells. If no such field exists in the attachment, extract the corresponding weekly summary section from the email body instead.
- For "下周计划", locate the "下周计划" or "下周工作计划" field in the Excel weekly report attachment (typically in the "项目周报" sheet). Extract the original text faithfully — do NOT invent, rewrite, or summarize. Preserve line separation for readability. Within each item, replace multiple consecutive spaces with a single comma for readability in table cells. If no such field exists in the attachment, extract the corresponding next-week plan section from the email body instead.

Input:
{formatted_full_text}

Output JSON schema:
{{
  "{RAW_EXTRACT_KEY}": [
    {{"{SOURCE_KEY}": "body-or-attachment", "{CONTENT_KEY}": "excerpt"}}
  ],
  "{CONSISTENCY_KEY}": "consistency result",
  "{PROJECT_NAME_KEY}": "project name",
  "{PROJECT_CODE_KEY}": "project code",
  "{PROJECT_CYCLE_KEY}": "project weekly report cycle (e.g. 2026-05-12 ~ 2026-05-18)",
  "{PROJECT_PROGRESS_KEY}": 0.75,
  "{MILESTONE_KEY}": "key milestone information",
  "项目整体进展": "extracted text from Excel 项目整体进展概况, or brief summary if unavailable",
  "{WEEKLY_SUMMARY_KEY}": "this week summary",
  "{NEXT_WEEK_PLAN_KEY}": "next week plan"
}}
""".strip()


def get_risk_extract_prompt(formatted_full_text: str) -> str:
    """生成显式风险抽取提示词。"""
    return f"""
You are extracting explicit weekly report risks from email content and attachments.
Return strict JSON with the exact keys below.

Input:
{formatted_full_text}

Output JSON schema:
{{
  "{RAW_EXTRACT_KEY}": [
    {{"{SOURCE_KEY}": "body-or-attachment", "{CONTENT_KEY}": "risk excerpt"}}
  ],
  "{CONSISTENCY_KEY}": "consistency result",
  "{RISK_DETAIL_KEY}": [
    {{
      "{SEQ_KEY}": 1,
      "{RISK_TYPE_KEY}": "risk type",
      "{RISK_LEVEL_KEY}": "high/medium/low",
      "{RISK_DESC_KEY}": "risk description",
      "{RISK_ACTION_KEY}": "mitigation measures",
      "{RISK_OWNER_KEY}": "owner",
      "{RISK_DUE_DATE_KEY}": "planned resolution date"
    }}
  ]
}}
""".strip()


async def extract_progress_from_report(week_report_md: str) -> tuple[dict[str, Any] | None, str | None]:
    """抽取进度信息。"""
    try:
        response = await llm_client.acall(get_progress_extract_prompt(week_report_md))
        if not response:
            message = "Progress extraction failed: empty LLM response"
            logger.error(message)
            return None, message
        return _load_json_object_response(response, "Progress extraction"), None
    except Exception as exc:
        message = f"Progress extraction failed: {exc}"
        logger.error(message)
        return None, message


async def extract_risk_from_report(week_report_md: str) -> tuple[dict[str, Any] | None, str | None]:
    """抽取显式风险。"""
    try:
        response = await llm_client.acall(get_risk_extract_prompt(week_report_md))
        if not response:
            message = "Risk extraction failed: empty LLM response"
            logger.error(message)
            return None, message
        return _load_json_object_response(response, "Risk extraction"), None
    except Exception as exc:
        message = f"Risk extraction failed: {exc}"
        logger.error(message)
        return None, message


async def process_email_analysis_with_errors(formatted_content: str, email_subject: str) -> tuple[dict[str, Any], list[str]]:
    """执行完整两段分析，并返回结构化结果和错误列表。"""
    start_time = time.time()
    logger.info(f"Starting weekly report analysis: {email_subject}")

    result: dict[str, Any] = {}
    errors: list[str] = []
    try:
        progress_response, risk_response = await asyncio.gather(
            extract_progress_from_report(formatted_content),
            extract_risk_from_report(formatted_content),
        )
        progress_result, progress_error = progress_response
        risk_result, risk_error = risk_response

        if progress_result is not None:
            result[PROGRESS_KEY] = progress_result
        if progress_error is not None:
            errors.append(progress_error)

        if risk_result is not None:
            result[RISK_KEY] = risk_result
        if risk_error is not None:
            errors.append(risk_error)
    except Exception as exc:
        message = f"Weekly report analysis failed for {email_subject}: {exc}"
        logger.error(message)
        errors.append(message)

    logger.info(f"Completed weekly report analysis: {email_subject}; elapsed={time.time() - start_time:.2f}s")
    return result, errors
