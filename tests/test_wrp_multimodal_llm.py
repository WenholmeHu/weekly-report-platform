"""多模态 LLM 客户端测试。

所有测试 monkeypatch httpx，不访问真实 LLM 服务。
使用 asyncio.run() 调用异步方法，不依赖 pytest-asyncio。
"""

import asyncio

import pytest

from weekly_report_platform.domain.mail_analysis.llm_client import LLMServiceError
from weekly_report_platform.domain.mail_analysis.multimodal_llm import MultimodalLLMClient


@pytest.fixture(autouse=True)
def _setup_vision_env(monkeypatch):
    """为所有测试设置 fake vision 环境变量，避免 URL 校验提前失败。"""
    monkeypatch.setenv("VISION_URL", "http://fake-vision-api/v1/chat/completions")
    monkeypatch.setenv("VISION_AUTHORIZATION", "Bearer fake-key")


def _make_response_json(text: str) -> dict:
    """构造一个正常的 LLM 响应 JSON。"""
    return {
        "choices": [
            {"message": {"content": text, "role": "assistant"}}
        ]
    }


def _fake_post_factory(response_json: dict | None = None, *, status_code: int = 200):
    """构造一个 monkeypatch 用的 fake AsyncClient.post 方法。"""
    import httpx

    async def fake_post(self, url, **kwargs):
        request = httpx.Request("POST", url)
        if status_code != 200:
            response = httpx.Response(status_code=status_code, text="error body", request=request)
            raise httpx.HTTPStatusError(
                message=f"HTTP {status_code}",
                request=request,
                response=response,
            )
        return httpx.Response(status_code=200, json=response_json, request=request)

    return fake_post


def _make_client(*, model: str | None = None) -> MultimodalLLMClient:
    """创建一个使用默认 vision 配置的 MultimodalLLMClient 实例。"""
    return MultimodalLLMClient(model=model)


# ---- 正常响应 ----

def test_acall_returns_text_on_normal_response(monkeypatch) -> None:
    """正常多模态响应应正确提取文本。"""
    monkeypatch.setattr(
        "httpx.AsyncClient.post",
        _fake_post_factory(_make_response_json("这是一张进度图表")),
    )
    client = _make_client()
    result = asyncio.run(
        client.acall(
            text_prompt="请描述这张图片",
            image_data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
            image_content_type="image/png",
        )
    )
    assert result == "这是一张进度图表"


# ---- payload 构造验证 ----

def test_acall_sends_multimodal_payload(monkeypatch) -> None:
    """验证请求 payload 包含多模态 content 数组。"""
    import httpx

    captured_payload = None

    async def capturing_post(self, url, **kwargs):
        nonlocal captured_payload
        captured_payload = kwargs.get("json", {})
        return httpx.Response(
            status_code=200, json=_make_response_json("ok"),
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("httpx.AsyncClient.post", capturing_post)
    client = _make_client()
    asyncio.run(
        client.acall(
            text_prompt="描述",
            image_data=b"imgdata",
            image_content_type="image/png",
        )
    )
    assert captured_payload is not None
    content = captured_payload["messages"][0]["content"]
    assert isinstance(content, list)
    assert len(content) == 2
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["detail"] == "high"
    assert "data:image/png;base64," in content[1]["image_url"]["url"]
    assert "n" not in captured_payload
    assert "stream" not in captured_payload


# ---- 空响应 ----

def test_acall_raises_on_empty_choices(monkeypatch) -> None:
    """空 choices 应抛出 LLMServiceError。"""
    monkeypatch.setattr(
        "httpx.AsyncClient.post",
        _fake_post_factory({"choices": []}),
    )
    client = _make_client()
    with pytest.raises(LLMServiceError, match="choices"):
        asyncio.run(
            client.acall(
                text_prompt="描述",
                image_data=b"img",
                image_content_type="image/png",
            )
        )


# ---- HTTP 错误 ----

def test_acall_raises_on_http_error(monkeypatch) -> None:
    """HTTP 错误应抛出 LLMServiceError 并包含状态码。"""
    monkeypatch.setattr(
        "httpx.AsyncClient.post",
        _fake_post_factory(None, status_code=500),
    )
    client = _make_client()
    with pytest.raises(LLMServiceError, match="HTTP 500"):
        asyncio.run(
            client.acall(
                text_prompt="描述",
                image_data=b"img",
                image_content_type="image/png",
            )
        )


# ---- 网络错误 ----

def test_acall_raises_on_network_error(monkeypatch) -> None:
    """网络错误应抛出 LLMServiceError。"""
    import httpx

    async def network_error_post(self, url, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("httpx.AsyncClient.post", network_error_post)
    client = _make_client()
    with pytest.raises(LLMServiceError, match="network error"):
        asyncio.run(
            client.acall(
                text_prompt="描述",
                image_data=b"img",
                image_content_type="image/png",
            )
        )


# ---- 未配置 URL ----

def test_acall_raises_when_url_not_configured(monkeypatch) -> None:
    """VISION_URL 未配置时应抛出 LLMServiceError。"""
    monkeypatch.setenv("VISION_URL", "")
    client_no_url = _make_client()
    with pytest.raises(LLMServiceError, match="VISION_URL"):
        asyncio.run(
            client_no_url.acall(
                text_prompt="描述",
                image_data=b"img",
                image_content_type="image/png",
            )
        )


# ---- model 覆盖 ----

def test_model_override() -> None:
    """model 参数覆盖应生效。"""
    client_override = _make_client(model="custom-model-vision")
    assert client_override.model == "custom-model-vision"


# ---- 非 JSON 响应 ----

def test_acall_handles_non_json_response(monkeypatch) -> None:
    """非 JSON 响应应正确处理（不要求 JSON 格式返回）。"""
    monkeypatch.setattr(
        "httpx.AsyncClient.post",
        _fake_post_factory(_make_response_json("纯文本描述，不是 JSON")),
    )
    client = _make_client()
    result = asyncio.run(
        client.acall(
            text_prompt="描述",
            image_data=b"img",
            image_content_type="image/png",
        )
    )
    assert result == "纯文本描述，不是 JSON"


# ---- 响应 content 为 text block 数组 ----

def test_acall_handles_text_block_array(monkeypatch) -> None:
    """响应 content 为 text block 数组时应正确合并提取。"""
    response_json = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "图片描述: "},
                        {"type": "text", "text": "这是进度图表"},
                    ]
                }
            }
        ]
    }
    monkeypatch.setattr(
        "httpx.AsyncClient.post",
        _fake_post_factory(response_json),
    )
    client = _make_client()
    result = asyncio.run(
        client.acall(
            text_prompt="描述",
            image_data=b"img",
            image_content_type="image/png",
        )
    )
    assert result == "图片描述: 这是进度图表"


# ---- 批量调用 ----

def test_acall_batch_returns_all_results(monkeypatch) -> None:
    """批量调用时所有图片都能得到响应。"""
    import httpx

    call_count = 0
    responses = ["描述1", "描述2", "描述3"]

    async def batch_post(self, url, **kwargs):
        nonlocal call_count
        idx = call_count
        call_count += 1
        return httpx.Response(
            status_code=200, json=_make_response_json(responses[idx]),
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("httpx.AsyncClient.post", batch_post)
    client = _make_client()
    images = [
        (b"img1", "image/png"),
        (b"img2", "image/jpeg"),
        (b"img3", "image/gif"),
    ]
    results = asyncio.run(
        client.acall_batch(text_prompt="描述图片", images=images)
    )
    assert results == ["描述1", "描述2", "描述3"]


# ---- 批量调用中部分失败 ----

def test_acall_batch_handles_partial_failure(monkeypatch) -> None:
    """批量调用中部分图片失败不应影响其他图片的结果。"""
    import httpx

    call_count = 0

    async def partial_fail_post(self, url, **kwargs):
        nonlocal call_count
        idx = call_count
        call_count += 1
        request = httpx.Request("POST", url)
        if idx == 1:
            response = httpx.Response(status_code=500, text="server error", request=request)
            raise httpx.HTTPStatusError(
                message="HTTP 500",
                request=request,
                response=response,
            )
        return httpx.Response(status_code=200, json=_make_response_json(f"描述{idx}"), request=request)

    monkeypatch.setattr("httpx.AsyncClient.post", partial_fail_post)
    client = _make_client()
    images = [
        (b"img1", "image/png"),
        (b"img2", "image/jpeg"),
        (b"img3", "image/gif"),
    ]
    results = asyncio.run(
        client.acall_batch(text_prompt="描述图片", images=images)
    )
    assert len(results) == 3
    assert results[0] == "描述0"
    assert isinstance(results[1], LLMServiceError)
    assert results[2] == "描述2"
