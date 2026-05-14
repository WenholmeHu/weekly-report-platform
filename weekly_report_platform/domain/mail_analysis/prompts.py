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
HIDDEN_RISK_KEY = "隐藏风险"
ANALYSIS_RESULT_KEY = "分析结果"
SUMMARY_KEY = "综合总结"
CONSISTENCY_KEY = "一致性分析"
RAW_EXTRACT_KEY = "原文抽取"
RISK_DETAIL_KEY = "风险详情"
SOURCE_KEY = "来源"
CONTENT_KEY = "原文内容"
RISK_DESC_KEY = "风险描述"
RISK_TYPE_KEY = "风险类型"
RISK_ACTION_KEY = "风险措施和最新进展"
RISK_OWNER_KEY = "风险责任人"
RISK_SUMMARY_KEY = "风险归纳"
SEQ_KEY = "序号"


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


def _load_json_object_response(response: str, stage_name: str) -> dict[str, Any]:
    """把模型输出解析成 JSON object。"""
    parsed = json.loads(clean_json_response(response))
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
- If [Image Content] section is present, also extract progress information from image descriptions.
- Image descriptions are provided by a vision model; treat them as supplementary to the email text.

Input:
{formatted_full_text}

Output JSON schema:
{{
  "{RAW_EXTRACT_KEY}": [
    {{"{SOURCE_KEY}": "body-or-attachment", "{CONTENT_KEY}": "excerpt"}}
  ],
  "{ANALYSIS_RESULT_KEY}": {{
    "{SUMMARY_KEY}": "concise summary",
    "{CONSISTENCY_KEY}": "consistency result"
  }}
}}
""".strip()


def get_risk_extract_prompt(formatted_full_text: str) -> str:
    """生成显式风险抽取提示词。"""
    return f"""
You are extracting explicit weekly report risks from email content and attachments.
Return strict JSON with the exact keys below.

Input:
{formatted_full_text}

- If [Image Content] section is present, also identify risks mentioned in image descriptions.

Output JSON schema:
{{
  "{RAW_EXTRACT_KEY}": [
    {{"{SOURCE_KEY}": "body-or-attachment", "{CONTENT_KEY}": "risk excerpt"}}
  ],
  "{CONSISTENCY_KEY}": "consistency result",
  "{RISK_DETAIL_KEY}": [
    {{
      "{SEQ_KEY}": 1,
      "{RISK_DESC_KEY}": "risk description",
      "{RISK_TYPE_KEY}": "risk type",
      "{RISK_ACTION_KEY}": "mitigation and latest progress",
      "{RISK_OWNER_KEY}": "owner"
    }}
  ]
}}
""".strip()


def get_hidden_risk_extract_prompt(present_risks: str, formatted_full_text: str) -> str:
    """生成隐藏风险抽取提示词。"""
    return f"""
You are identifying hidden risks outside the explicitly labeled risk sections.
Avoid duplicates with the known risks below.

Known risks:
{present_risks}

Input:
{formatted_full_text}

- Consider image descriptions as part of the input when identifying hidden risks.

Return a strict JSON array:
[
  {{
    "{SEQ_KEY}": 1,
    "{SOURCE_KEY}": "body-or-attachment",
    "{RISK_DESC_KEY}": "hidden risk description",
    "{RISK_TYPE_KEY}": "risk type",
    "{RISK_SUMMARY_KEY}": "short hidden risk summary",
    "{RISK_ACTION_KEY}": "mitigation and latest progress"
  }}
]
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


async def extract_hidden_risk_from_report(
    week_report_md: str,
    risk_result: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """抽取隐藏风险，并尽量避免与显式风险重复。"""
    try:
        present_risks = ""
        if risk_result:
            raw_extractions = risk_result.get(RAW_EXTRACT_KEY, [])
            if isinstance(raw_extractions, list) and raw_extractions:
                rows = []
                for extraction in raw_extractions:
                    if not isinstance(extraction, dict):
                        continue
                    source = extraction.get(SOURCE_KEY, "")
                    content = extraction.get(CONTENT_KEY, "")
                    if content:
                        rows.append(f"[Source: {source}]\n{content}")
                present_risks = "\n\n".join(rows)
        if not present_risks:
            present_risks = "(no known risks)"

        response = await llm_client.acall(get_hidden_risk_extract_prompt(present_risks, week_report_md))
        if not response:
            message = "Hidden risk extraction failed: empty LLM response"
            logger.error(message)
            return None, message
        return _load_json_array_response(response, "Hidden risk extraction"), None
    except Exception as exc:
        message = f"Hidden risk extraction failed: {exc}"
        logger.error(message)
        return None, message


async def process_email_analysis_with_errors(formatted_content: str, email_subject: str) -> tuple[dict[str, Any], list[str]]:
    """执行完整三段分析，并返回结构化结果和错误列表。"""
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

        hidden_risk_result, hidden_risk_error = await extract_hidden_risk_from_report(formatted_content, risk_result)

        if progress_result is not None:
            result[PROGRESS_KEY] = progress_result
        if progress_error is not None:
            errors.append(progress_error)

        if risk_result is not None:
            result[RISK_KEY] = risk_result
        if risk_error is not None:
            errors.append(risk_error)

        if hidden_risk_result is not None:
            result[HIDDEN_RISK_KEY] = hidden_risk_result
        if hidden_risk_error is not None:
            errors.append(hidden_risk_error)
    except Exception as exc:
        message = f"Weekly report analysis failed for {email_subject}: {exc}"
        logger.error(message)
        errors.append(message)

    logger.info(f"Completed weekly report analysis: {email_subject}; elapsed={time.time() - start_time:.2f}s")
    return result, errors
