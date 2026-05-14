"""图片提取模块。

从解析后的邮件数据中提取所有候选内嵌图片（cid 引用和 base64 data URI）。
只做提取和分类，不做任何有效性判断。
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExtractedImage:
    """从邮件中提取的一张图片。"""

    data: bytes  # 图片原始字节
    content_type: str  # MIME 类型，如 "image/png"
    filename: str  # 文件名，如 "chart001.png"
    size: int  # data 的字节数
    content_id: str = ""  # Content-ID（cid 引用），如 "image001@xxx"
    disposition: str = "inline"  # "inline" 或 "attachment"
    origin: str = ""  # 图片来源："email_html_inline" / "email_html_base64"


# 正则：匹配 <img> 标签中 base64 data URI 的 src 属性
_BASE64_IMG_RE = re.compile(
    r'<img[^>]+src=["\']data:image/([^;]+);base64,([^"\']+)["\']',
    re.IGNORECASE,
)

# 正则：匹配 HTML 中的 cid: 引用
_CID_IMG_RE = re.compile(
    r'<img[^>]+src=["\']cid:([^"\']+)["\']',
    re.IGNORECASE,
)


def extract_images_from_email(email_data: dict[str, Any]) -> list[ExtractedImage]:
    """从解析后的邮件数据中提取所有候选图片。

    图片来源（两种）：
    1. CID 引用图片：HTML 正文中 <img src="cid:xxx"> 对应的 MIME inline part
    2. Base64 data URI 图片：HTML 中 <img src="data:image/png;base64,...">

    不提取：HTML 中远程 https:// 图片、附件中的图片。

    Args:
        email_data: 来自 _parse_email() 返回值的 dict。

    Returns:
        list[ExtractedImage]，未提取到图片时返回空列表。
    """
    results: list[ExtractedImage] = []

    html_body = email_data.get("html_body", "")
    if not html_body:
        return results

    # 1. 提取 base64 data URI 图片
    base64_images = _extract_base64_images(html_body)
    results.extend(base64_images)

    # 2. 提取 CID 引用图片
    cid_images = _extract_cid_images(html_body, email_data)
    results.extend(cid_images)

    return results


def _extract_base64_images(html_body: str) -> list[ExtractedImage]:
    """从 HTML 中提取 base64 data URI 图片。"""
    results: list[ExtractedImage] = []
    for idx, match in enumerate(_BASE64_IMG_RE.finditer(html_body), start=1):
        content_type_suffix = match.group(1)
        base64_data = match.group(2)
        try:
            image_bytes = base64.b64decode(base64_data)
        except Exception:
            continue

        filename = f"base64_image_{idx}.{content_type_suffix.split('/')[-1]}"
        results.append(
            ExtractedImage(
                data=image_bytes,
                content_type=f"image/{content_type_suffix}",
                filename=filename,
                size=len(image_bytes),
                content_id="",
                disposition="inline",
                origin="email_html_base64",
            )
        )
    return results


def _extract_cid_images(
    html_body: str,
    email_data: dict[str, Any],
) -> list[ExtractedImage]:
    """从 HTML 中 cid: 引用匹配 MIME inline part。"""
    inline_images: list[dict[str, Any]] = email_data.get("inline_images", [])
    if not inline_images:
        return []

    # 构建 content_id -> inline_image 的映射
    cid_map: dict[str, dict[str, Any]] = {}
    for img_info in inline_images:
        cid = img_info.get("content_id", "")
        if cid:
            cid_map[cid] = img_info

    results: list[ExtractedImage] = []
    for idx, match in enumerate(_CID_IMG_RE.finditer(html_body), start=1):
        cid_ref = match.group(1)
        img_info = cid_map.get(cid_ref)
        if img_info is None:
            continue

        image_bytes = img_info["data"]
        content_type = img_info.get("content_type", "image/png")
        filename = f"cid_image_{idx}.{content_type.split('/')[-1]}"

        results.append(
            ExtractedImage(
                data=image_bytes,
                content_type=content_type,
                filename=filename,
                size=img_info.get("size", len(image_bytes)),
                content_id=cid_ref,
                disposition="inline",
                origin="email_html_inline",
            )
        )
    return results
