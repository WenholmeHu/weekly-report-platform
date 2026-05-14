"""图片过滤模块。

纯函数，接收 list[ExtractedImage]，返回 list[ValidImage]。
按大小、尺寸、宽高比等启发式规则剔除无效图片。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from weekly_report_platform.domain.mail_analysis.image_extractor import ExtractedImage


@dataclass(frozen=True)
class ImageFilterConfig:
    """图片过滤配置。"""

    min_file_size: int = 5000  # 最小文件大小（字节），低于此阈值的图片通常是 logo/二维码/签名图
    min_dimension: int = 10  # 最小宽/高（像素）
    max_aspect_ratio: float = 100.0  # 最大宽高比


@dataclass(frozen=True)
class ValidImage:
    """通过过滤的图片，携带上下文片段。"""

    data: bytes
    content_type: str
    filename: str
    size: int
    content_id: str = ""
    disposition: str = "inline"
    origin: str = ""
    context_snippet: str = ""  # HTML 中图片附近的上下文文本


def _parse_image_dimensions(data: bytes, content_type: str) -> tuple[int, int] | None:
    """从图片 bytes 中解析宽高（像素）。

    支持 PNG、JPEG、GIF 三种格式的 header 手动解析。
    返回 None 表示无法解析。
    """
    if content_type == "image/png" or content_type.endswith("/png"):
        return _parse_png_dimensions(data)
    elif content_type == "image/jpeg" or content_type in ("image/jpg", "image/pjpeg"):
        return _parse_jpeg_dimensions(data)
    elif content_type == "image/gif":
        return _parse_gif_dimensions(data)
    return None


def _parse_png_dimensions(data: bytes) -> tuple[int, int] | None:
    """从 PNG IHDR 块中提取宽高。

    PNG header: 8 bytes signature + IHDR chunk:
    - 4 bytes: chunk length
    - 4 bytes: "IHDR"
    - 4 bytes: width (big-endian uint32)
    - 4 bytes: height (big-endian uint32)
    """
    try:
        # PNG signature is 8 bytes, IHDR starts at offset 8
        if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        width = struct.unpack(">I", data[16:20])[0]
        height = struct.unpack(">I", data[20:24])[0]
        return (width, height)
    except Exception:
        return None


def _parse_jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """从 JPEG SOF0 段中提取宽高。

    JPEG header: SOI (0xFFD8) 后各 marker 段。
    SOF0 marker: 0xFFC0, 后面 2 bytes length, 1 byte precision,
    2 bytes height, 2 bytes width (both big-endian).
    """
    try:
        if len(data) < 4 or data[:2] != b"\xff\xd8":
            return None
        offset = 2
        while offset < len(data) - 1:
            marker = data[offset]
            if marker != 0xFF:
                return None
            marker_type = data[offset + 1]
            # SOF0 (0xC0) 或 SOF2 (0xC2，渐进式 JPEG)
            if marker_type in (0xC0, 0xC2):
                # length(2) + precision(1) + height(2) + width(2)
                if offset + 9 > len(data):
                    return None
                height = struct.unpack(">H", data[offset + 5:offset + 7])[0]
                width = struct.unpack(">H", data[offset + 7:offset + 9])[0]
                return (width, height)
            # 跳过非 SOF marker 段
            if marker_type in (0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9):
                # RST 和 SOI/EOI 段没有 payload
                offset += 2
            elif marker_type == 0x00:
                # 填充字节
                offset += 2
            else:
                if offset + 4 > len(data):
                    return None
                segment_length = struct.unpack(">H", data[offset + 2:offset + 4])[0]
                offset += 2 + segment_length
        return None
    except Exception:
        return None


def _parse_gif_dimensions(data: bytes) -> tuple[int, int] | None:
    """从 GIF header 的逻辑屏幕描述符中提取宽高。

    GIF header: "GIF87a" 或 "GIF89a" (6 bytes),
    后 2 bytes: width (little-endian), 2 bytes: height (little-endian).
    """
    try:
        if len(data) < 10 or (data[:6] != b"GIF87a" and data[:6] != b"GIF89a"):
            return None
        width = struct.unpack("<H", data[6:8])[0]
        height = struct.unpack("<H", data[8:10])[0]
        return (width, height)
    except Exception:
        return None


def filter_images(
    images: list[ExtractedImage],
    config: ImageFilterConfig | None = None,
) -> list[ValidImage]:
    """过滤无效图片，返回有效的候选图片列表。

    过滤规则（按顺序执行，任何一条命中即剔除）：
    1. 大小过小：文件大小 < min_file_size
    2. 尺寸过小：宽或高 < min_dimension
    3. 极端宽高比：宽/高 > max_aspect_ratio 或 < 1/max_aspect_ratio
    """
    if not images:
        return []

    effective_config = config or ImageFilterConfig()
    results: list[ValidImage] = []

    for img in images:
        # 规则 1：大小过小
        if img.size < effective_config.min_file_size:
            continue

        # 规则 2 和 3：尺寸相关过滤
        dimensions = _parse_image_dimensions(img.data, img.content_type)
        if dimensions is not None:
            width, height = dimensions
            # 规则 2：尺寸过小
            if width < effective_config.min_dimension or height < effective_config.min_dimension:
                continue
            # 规则 3：极端宽高比
            if height == 0 or width == 0:
                continue
            aspect = width / height
            if aspect > effective_config.max_aspect_ratio or aspect < 1 / effective_config.max_aspect_ratio:
                continue

        results.append(
            ValidImage(
                data=img.data,
                content_type=img.content_type,
                filename=img.filename,
                size=img.size,
                content_id=img.content_id,
                disposition=img.disposition,
                origin=img.origin,
                context_snippet="",
            )
        )

    return results