"""图片过滤模块测试。"""

from __future__ import annotations

from weekly_report_platform.domain.mail_analysis.image_extractor import ExtractedImage
from weekly_report_platform.domain.mail_analysis.image_filter import (
    ImageFilterConfig,
    ValidImage,
    filter_images,
    _parse_image_dimensions,
)


def _make_png_bytes(width: int = 100, height: int = 100) -> bytes:
    """构造最小的合法 PNG 图片 bytes（含 IHDR 块）。"""
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = (
        width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )
    ihdr_length = len(ihdr_data).to_bytes(4, "big")
    ihdr_type = b"IHDR"
    import zlib
    ihdr_crc = zlib.crc32(ihdr_type + ihdr_data).to_bytes(4, "big")
    ihdr_chunk = ihdr_length + ihdr_type + ihdr_data + ihdr_crc

    iend_length = b"\x00\x00\x00\x00"
    iend_type = b"IEND"
    iend_crc = zlib.crc32(b"IEND").to_bytes(4, "big")
    iend_chunk = iend_length + iend_type + iend_crc

    return signature + ihdr_chunk + iend_chunk


def _make_jpeg_bytes(width: int = 100, height: int = 100) -> bytes:
    """构造最小的 JPEG 图片 bytes（含 SOF0 段）。"""
    # SOI marker
    data = b"\xff\xd8"
    # SOF0 marker
    sof0_data = (
        b"\x08"  # precision
        + height.to_bytes(2, "big")  # height
        + width.to_bytes(2, "big")  # width
        + b"\x01"  # num_components
        + b"\x01\x11\x00"  # component 1
    )
    sof0_length = (len(sof0_data) + 2).to_bytes(2, "big")
    data += b"\xff\xc0" + sof0_length + sof0_data
    # EOI marker
    data += b"\xff\xd9"
    return data


def test_wrp_image_filter_returns_empty_for_empty_list() -> None:
    """空列表输入返回空列表。"""
    assert filter_images([]) == []


def test_wrp_image_filter_removes_small_file_size() -> None:
    """文件大小 < 5000 字节被过滤（logo/二维码/签名图等）。"""
    # 构造一个大尺寸但数据量很小的图片（PNG header + 大尺寸 IHDR）
    png_data = _make_png_bytes(width=1000, height=1000)
    # 正常情况下 PNG 至少几百字节，这里构造一个小文件
    tiny_data = png_data[:100]  # 截断后 < 500 字节
    img = ExtractedImage(
        data=tiny_data,
        content_type="image/png",
        filename="tiny.png",
        size=len(tiny_data),
        origin="email_html_base64",
    )

    result = filter_images([img])

    assert result == []


def test_wrp_image_filter_removes_small_dimension_image() -> None:
    """宽或高 < 10 像素被过滤。"""
    png_data = _make_png_bytes(width=5, height=100)  # 宽 5 < 10
    img = ExtractedImage(
        data=png_data,
        content_type="image/png",
        filename="narrow.png",
        size=len(png_data),
        origin="email_html_base64",
    )

    result = filter_images([img])

    assert result == []


def test_wrp_image_filter_removes_extreme_aspect_ratio() -> None:
    """极端宽高比被过滤。"""
    # 宽 1000, 高 1 -> 宽高比 1000 > 100
    png_data = _make_png_bytes(width=1000, height=1)
    img = ExtractedImage(
        data=png_data,
        content_type="image/png",
        filename="thin_line.png",
        size=len(png_data),
        origin="email_html_base64",
    )

    result = filter_images([img])

    assert result == []


def test_wrp_image_filter_passes_normal_image() -> None:
    """正常图片通过过滤。"""
    png_data = _make_png_bytes(width=100, height=100)
    padded_data = png_data + b"\x00" * max(0, 5000 - len(png_data) + 1)
    img = ExtractedImage(
        data=padded_data,
        content_type="image/png",
        filename="normal.png",
        size=len(padded_data),
        origin="email_html_base64",
    )

    result = filter_images([img])

    assert len(result) == 1
    assert result[0].filename == "normal.png"


def test_wrp_image_filter_mixed_valid_and_invalid() -> None:
    """混合有效和无效图片时只返回有效图片。"""
    # 无效：小文件
    tiny_img = ExtractedImage(
        data=b"\x00" * 100,
        content_type="image/png",
        filename="tiny.png",
        size=100,
        origin="email_html_base64",
    )
    # 有效：正常尺寸、足够大
    png_data = _make_png_bytes(width=100, height=100)
    padded_data = png_data + b"\x00" * max(0, 5000 - len(png_data) + 1)
    valid_img = ExtractedImage(
        data=padded_data,
        content_type="image/png",
        filename="valid.png",
        size=len(padded_data),
        origin="email_html_base64",
    )
    # 无效：极端宽高比
    thin_png = _make_png_bytes(width=1000, height=1)
    thin_img = ExtractedImage(
        data=thin_png,
        content_type="image/png",
        filename="thin.png",
        size=len(thin_png),
        origin="email_html_base64",
    )

    result = filter_images([tiny_img, valid_img, thin_img])

    assert len(result) == 1
    assert result[0].filename == "valid.png"


def test_wrp_image_filter_custom_config() -> None:
    """使用自定义 ImageFilterConfig 能调整阈值。"""
    png_data = _make_png_bytes(width=100, height=100)
    padded_data = png_data + b"\x00" * max(0, 5000 - len(png_data) + 1)
    img = ExtractedImage(
        data=padded_data,
        content_type="image/png",
        filename="normal.png",
        size=len(padded_data),
        origin="email_html_base64",
    )

    # 用非常严格的阈值过滤正常图片
    strict_config = ImageFilterConfig(
        min_file_size=10000,  # 远超图片大小
        min_dimension=10,
        max_aspect_ratio=100.0,
    )

    result = filter_images([img], config=strict_config)

    assert result == []


def test_wrp_image_filter_returns_valid_image_with_metadata() -> None:
    """通过过滤的图片返回 ValidImage 类型，携带原图元数据。"""
    png_data = _make_png_bytes(width=100, height=100)
    padded_data = png_data + b"\x00" * max(0, 5000 - len(png_data) + 1)
    img = ExtractedImage(
        data=padded_data,
        content_type="image/png",
        filename="test.png",
        size=len(padded_data),
        content_id="cid001",
        origin="email_html_inline",
    )

    result = filter_images([img])

    assert len(result) == 1
    valid = result[0]
    assert isinstance(valid, ValidImage)
    assert valid.content_id == "cid001"
    assert valid.origin == "email_html_inline"
    assert valid.context_snippet == ""


def test_wrp_parse_image_dimensions_png() -> None:
    """PNG 图片正确解析宽高。"""
    png_data = _make_png_bytes(width=800, height=600)
    width, height = _parse_image_dimensions(png_data, "image/png")
    assert width == 800
    assert height == 600


def test_wrp_parse_image_dimensions_jpeg() -> None:
    """JPEG 图片正确解析宽高。"""
    jpeg_data = _make_jpeg_bytes(width=640, height=480)
    width, height = _parse_image_dimensions(jpeg_data, "image/jpeg")
    assert width == 640
    assert height == 480


def test_wrp_parse_image_dimensions_gif() -> None:
    """GIF 图片正确解析宽高。"""
    # GIF89a header
    gif_data = (
        b"GIF89a"
        + (320).to_bytes(2, "little")  # width
        + (240).to_bytes(2, "little")  # height
        + b"\x00" * 100  # padding
    )
    width, height = _parse_image_dimensions(gif_data, "image/gif")
    assert width == 320
    assert height == 240


def test_wrp_parse_image_dimensions_unsupported_format() -> None:
    """不支持的格式返回 None。"""
    data = b"\x00" * 100
    assert _parse_image_dimensions(data, "image/webp") is None


def test_wrp_parse_image_dimensions_corrupt_data() -> None:
    """损坏的数据返回 None。"""
    assert _parse_image_dimensions(b"not_an_image", "image/png") is None


def test_wrp_parse_image_dimensions_empty() -> None:
    """空数据返回 None。"""
    assert _parse_image_dimensions(b"", "image/png") is None
