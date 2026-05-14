"""图片提取模块测试。"""

from __future__ import annotations

import base64
from email.message import EmailMessage

from weekly_report_platform.domain.mail_analysis import email_parser
from weekly_report_platform.domain.mail_analysis.image_extractor import ExtractedImage, extract_images_from_email


def _build_email_service() -> email_parser.EmailService:
    # 绕过 __init__，避免测试时加载环境配置。
    return object.__new__(email_parser.EmailService)


def _make_png_bytes(width: int = 100, height: int = 100) -> bytes:
    """构造最小的合法 PNG 图片 bytes（含 IHDR 块）。"""
    # PNG signature
    signature = b"\x89PNG\r\n\x1a\n"
    # IHDR chunk: width, height, bit_depth=8, color_type=2 (RGB), compression=0, filter=0, interlace=0
    ihdr_data = (
        width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )
    # chunk: length(4) + type(4) + data + crc(4)
    ihdr_length = len(ihdr_data).to_bytes(4, "big")
    ihdr_type = b"IHDR"
    import zlib
    ihdr_crc = zlib.crc32(ihdr_type + ihdr_data).to_bytes(4, "big")
    ihdr_chunk = ihdr_length + ihdr_type + ihdr_data + ihdr_crc

    # IEND chunk
    iend_length = b"\x00\x00\x00\x00"
    iend_type = b"IEND"
    iend_crc = zlib.crc32(b"IEND").to_bytes(4, "big")
    iend_chunk = iend_length + iend_type + iend_crc

    return signature + ihdr_chunk + iend_chunk


def test_wrp_image_extractor_returns_empty_for_plain_text_email() -> None:
    """纯文本邮件（无 HTML 正文）不提取图片。"""
    email_data = {
        "html_body": "",
        "text_body": "项目进度正常。",
        "inline_images": [],
    }

    result = extract_images_from_email(email_data)

    assert result == []


def test_wrp_image_extractor_returns_empty_for_html_without_images() -> None:
    """HTML 正文但没有图片标签时不提取。"""
    email_data = {
        "html_body": "<p>项目进度正常</p>",
        "inline_images": [],
    }

    result = extract_images_from_email(email_data)

    assert result == []


def test_wrp_image_extractor_extracts_base64_data_uri_images() -> None:
    """HTML 中有 base64 data URI 图片时正确提取。"""
    png_data = _make_png_bytes()
    base64_encoded = base64.b64encode(png_data).decode("ascii")
    html_body = f'<img src="data:image/png;base64,{base64_encoded}">'

    email_data = {
        "html_body": html_body,
        "inline_images": [],
    }

    result = extract_images_from_email(email_data)

    assert len(result) == 1
    img = result[0]
    assert img.origin == "email_html_base64"
    assert img.content_type == "image/png"
    assert img.data == png_data
    assert img.disposition == "inline"


def test_wrp_image_extractor_skips_https_remote_images() -> None:
    """HTML 中远程 https 图片不提取。"""
    html_body = '<img src="https://example.com/chart.png">'

    email_data = {
        "html_body": html_body,
        "inline_images": [],
    }

    result = extract_images_from_email(email_data)

    assert result == []


def test_wrp_image_extractor_extracts_cid_reference_images() -> None:
    """HTML 中有 cid: 引用时找到对应的 MIME inline part 并提取。"""
    png_data = _make_png_bytes()
    html_body = '<img src="cid:image001@example.com">'
    inline_images = [
        {
            "content_id": "image001@example.com",
            "content_type": "image/png",
            "data": png_data,
            "size": len(png_data),
        },
    ]

    email_data = {
        "html_body": html_body,
        "inline_images": inline_images,
    }

    result = extract_images_from_email(email_data)

    assert len(result) == 1
    img = result[0]
    assert img.origin == "email_html_inline"
    assert img.content_id == "image001@example.com"
    assert img.content_type == "image/png"
    assert img.data == png_data


def test_wrp_image_extractor_skips_unmatched_cid_references() -> None:
    """HTML 中 cid: 引用找不到对应 MIME part 时跳过。"""
    html_body = '<img src="cid:missing_id@example.com">'
    inline_images = [
        {
            "content_id": "image001@example.com",
            "content_type": "image/png",
            "data": _make_png_bytes(),
            "size": 100,
        },
    ]

    email_data = {
        "html_body": html_body,
        "inline_images": inline_images,
    }

    result = extract_images_from_email(email_data)

    assert result == []


def test_wrp_image_extractor_extracts_mixed_cid_and_base64_images() -> None:
    """混合 cid 和 base64 图片时全部正确提取。"""
    png_data = _make_png_bytes()
    base64_encoded = base64.b64encode(png_data).decode("ascii")
    html_body = (
        f'<img src="cid:image001@example.com">'
        f'<img src="data:image/png;base64,{base64_encoded}">'
    )
    inline_images = [
        {
            "content_id": "image001@example.com",
            "content_type": "image/png",
            "data": png_data,
            "size": len(png_data),
        },
    ]

    email_data = {
        "html_body": html_body,
        "inline_images": inline_images,
    }

    result = extract_images_from_email(email_data)

    assert len(result) == 2
    origins = [img.origin for img in result]
    assert "email_html_inline" in origins
    assert "email_html_base64" in origins


def test_wrp_image_extractor_skips_invalid_base64_data() -> None:
    """base64 数据无法解码时跳过该图片。"""
    html_body = '<img src="data:image/png;base64,not_valid_base64!!!">'

    email_data = {
        "html_body": html_body,
        "inline_images": [],
    }

    result = extract_images_from_email(email_data)

    assert result == []


def test_wrp_email_parser_collects_inline_images() -> None:
    """验证 _parse_email() 新增 inline_images 字段能正确收集 inline 图片 part。"""
    service = _build_email_service()
    message = EmailMessage()
    message["Subject"] = "项目周报"
    message["From"] = "pm@example.com"
    message["To"] = "team@example.com"
    message.set_content("项目进度正常。", charset="utf-8")

    png_data = _make_png_bytes()
    message.add_attachment(
        png_data,
        maintype="image",
        subtype="png",
        disposition="inline",
    )
    # EmailMessage 自动设置 Content-ID
    message.get_payload()[1]["Content-ID"] = "<image001@example.com>"

    parsed = service._parse_email(message.as_bytes())

    assert parsed is not None
    assert "inline_images" in parsed
    assert len(parsed["inline_images"]) == 1
    inline_img = parsed["inline_images"][0]
    assert inline_img["content_id"] == "image001@example.com"
    assert inline_img["content_type"] == "image/png"
    assert inline_img["data"] == png_data


def test_wrp_email_parser_inline_images_default_empty() -> None:
    """无 inline 图片的邮件返回 inline_images 为空列表。"""
    service = _build_email_service()
    message = EmailMessage()
    message["Subject"] = "项目周报"
    message["From"] = "pm@example.com"
    message["To"] = "team@example.com"
    message.set_content("项目进度正常。", charset="utf-8")

    parsed = service._parse_email(message.as_bytes())

    assert parsed is not None
    assert parsed["inline_images"] == []


def test_wrp_email_parser_inline_images_not_conflated_with_attachments() -> None:
    """inline 图片不混入 attachments 列表。"""
    service = _build_email_service()
    message = EmailMessage()
    message["Subject"] = "项目周报"
    message["From"] = "pm@example.com"
    message["To"] = "team@example.com"
    message.set_content("项目进度正常。", charset="utf-8")

    png_data = _make_png_bytes()
    # attachment 图片（Content-Disposition: attachment）应进入 attachments
    message.add_attachment(
        png_data,
        maintype="image",
        subtype="png",
        disposition="attachment",
        filename="chart.png",
    )

    parsed = service._parse_email(message.as_bytes())

    assert parsed is not None
    assert len(parsed["attachments"]) == 1
    assert parsed["attachments"][0]["filename"] == "chart.png"
    assert parsed["inline_images"] == []