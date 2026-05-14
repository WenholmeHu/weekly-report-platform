# 周报邮件图片链路开发设计文档

## 0. 文档定位

本文档指导 Codex/Claude Code 完成"周报邮件图片提取、过滤、多模态 LLM 分析、钉钉归档"的完整链路开发。开发前需通读本文档及 `docs/ai/project/context.md`、`docs/ai/project/rules.md`。

## 1. 目标概述

### 现状

当前 `email_parser.py` 解析邮件时只处理文本正文和结构化附件（Excel/Word/PDF/TXT），图片完全被丢弃：

- HTML 正文中的内嵌图片（`cid:` 引用、base64 data URI）未被提取
- MIME 中 `image/*` 类型的 part 未被识别
- 最终分析 JSON 和 LLM 提示词中都不包含图片信息

### 总目标

拆成两条独立但共享底层能力的线：

| 线 | 目标 | 状态 |
|---|---|---|
| 线 A | 图片提取 + 过滤 + 多模态 LLM 分析，结果注入现有分析 | **本次开发** |
| 线 B | 图片归档到钉钉多维表（attachment 字段） | **后续开发** |

两条线共享"图片提取 + 过滤"这一底层模块，但分析层和同步层各自独立。

### 本次不处理

- 正文 HTML 中的远程 `https://` 外部 URL 图片
- 附件中的图片（`Content-Disposition: attachment` 的 `image/*` part）
- 钉钉 attachment 字段写入（线 B，后续做）

## 2. 整体架构

```
email_data (来自 email_parser.py)
  ├─ 文本正文 -> format_email_for_analysis() -> formatted_content (现有，不变)
  └─ 图片提取 -> image_extractor.extract_images() -> raw images list
                    └─ image_filter.filter_images() -> valid images list
                          ├─ [线 A] image_analyzer.analyze_images() -> image description text
                          │        └─ 注入 formatted_content -> 现有三段LLM -> analysis JSON
                          │
                          └─ [线 B 后续] 图片落盘 + metadata 记录 -> analysis JSON source.images
                                   └─ dingtalk.py attachment 字段支持
```

## 3. 新增模块清单

| 文件 | 职责 | 目录 |
|---|---|---|
| `image_extractor.py` | 从 email_data 提取所有候选图片 | `domain/mail_analysis/` |
| `image_filter.py` | 有效性过滤，剔除无效图片 | `domain/mail_analysis/` |
| `multimodal_llm.py` | 多模态 LLM 调用封装 | `domain/mail_analysis/` |
| `image_analyzer.py` | 图片分析编排 | `domain/mail_analysis/` |

## 4. 数据模型

### 4.1 ExtractedImage（提取后的原始图片）

```python
@dataclass(frozen=True)
class ExtractedImage:
    """从邮件中提取的一张图片。"""
    data: bytes              # 图片原始字节
    content_type: str        # MIME 类型，如 "image/png"
    filename: str            # 文件名，如 "chart001.png"
    size: int                # data 的字节数
    content_id: str = ""     # Content-ID（cid 引用），如 "image001@xxx"
    disposition: str = "inline"  # "inline" 或 "attachment"
    origin: str = ""         # 图片来源："email_html_inline" / "email_html_base64"
```

### 4.2 ValidImage（过滤后的有效图片）

```python
@dataclass(frozen=True)
class ValidImage(ExtractedImage):
    """通过过滤的图片，携带上下文片段。"""
    context_snippet: str = ""  # HTML 中图片附近的上下文文本，辅助LLM理解
```

### 4.3 ImageAnalysisResult（单张图片分析结果）

```python
@dataclass(frozen=True)
class ImageAnalysisResult:
    """多模态 LLM 对单张图片的分析结果。"""
    filename: str        # 对应图片文件名
    description: str     # LLM 描述文本
    error: str = ""      # 分析失败时的错误信息
```

## 5. 模块详细设计

### 5.1 image_extractor.py — 图片提取

**职责**：从 `_parse_email()` 返回的 `email_data` dict 中提取所有候选图片。

**图片来源（两种）**：

1. **CID 引用图片**：HTML 正文中 `<img src="cid:xxx">` 对应的 MIME inline part
   - MIME part 带有 `Content-ID: <xxx>` 且 `Content-Disposition: inline`
   - MIME 类型为 `image/*`

2. **Base64 data URI 图片**：HTML 中 `<img src="data:image/png;base64,...">`

**不提取**：HTML 中远程 `https://` 图片、附件中的图片。

**输入**：`email_data: dict[str, Any]`（来自 `_parse_email()` 返回值）
**输出**：`list[ExtractedImage]`

**具体实现要点**：

```python
def extract_images_from_email(email_data: dict[str, Any]) -> list[ExtractedImage]:
    """从解析后的邮件数据中提取所有候选图片。"""
```

实现步骤：

1. 解析 HTML 正文（`email_data.get("html_body")`）中的 base64 data URI：
   - 正则匹配 `<img[^>]+src=["']data:image/([^;]+);base64,([^"']+)["']`
   - 解码 base64 -> origin="email_html_base64", disposition="inline"
2. 提取 CID 映射关系（从 HTML 中提取所有 `cid:xxx` 引用）
3. 从 `email_data.get("inline_images")` 中根据 Content-ID 匹配 -> origin="email_html_inline", disposition="inline"

注意：当前 `_parse_email()` 只把 `Content-Disposition: attachment` 或 `application/octet-stream` 的 part 放入 attachments 列表。inline 图片 part 在现有代码中被丢弃。因此需要修改 `_parse_email()` 来额外收集 inline 图片 part，放入 `inline_images` 字段。详见 6.2.1 节。

**注意**：图片提取阶段只做提取和分类，不做任何有效性判断。无效图片交由 `image_filter.py` 处理。

### 5.2 image_filter.py — 图片过滤

**职责**：纯函数，接收 `list[ExtractedImage]`，返回 `list[ValidImage]`。

**过滤规则**（按顺序执行，任何一条命中即剔除）：

| 规则 | 条件 | 剔除原因 |
|---|---|---|
| 大小过小 | 文件大小 < `MIN_FILE_SIZE`（默认 500 字节） | 可能是 1x1 透明像素或图标 |
| 尺寸过小 | 宽或高 < `MIN_DIMENSION`（默认 10 像素） | 装饰性图标/追踪像素 |
| 极端宽高比 | 宽/高 > `MAX_ASPECT_RATIO`（默认 100）或 < 1/100 | 可能是分隔线 |

**可访问的默认值配置**（通过 `dataclass` 参数传入，不读环境变量）：

```python
@dataclass(frozen=True)
class ImageFilterConfig:
    min_file_size: int = 500          # 最小文件大小（字节）
    min_dimension: int = 10           # 最小宽/高（像素）
    max_aspect_ratio: float = 100.0   # 最大宽高比
```

**核心接口**：

```python
def filter_images(images: list[ExtractedImage], config: ImageFilterConfig = None) -> list[ValidImage]:
    """过滤无效图片，返回有效的候选图片列表。"""
```

**实现要点**：

- 需要从图片 bytes 中解析宽高：建议用 Pillow 库，如果项目不引入 Pillow，可解析 PNG/JPEG/GIF 的 header 手动提取尺寸
- 过滤规则全部基于启发式判断，不依赖 LLM
- 被过滤掉的图片不记录日志（或只记录 debug 级别），避免干扰正常日志
- 过滤函数应该是纯函数，无副作用

### 5.3 multimodal_llm.py — 多模态 LLM 客户端

**职责**：封装对多模态 LLM 的调用，支持文本 + 图片内容。

**现有 `llm_client.py` 的限制**：

- 只发送纯文本 prompt：`messages: [{"content": prompt, "role": "user"}]`
- 多模态 LLM 需要 content 数组形式，例如：
  ```json
  {
    "content": [
      {"type": "text", "text": "请描述这张图片..."},
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,...", "detail": "high"}}
    ]
  }
  ```

**设计方案**：不修改现有 `llm_client.py`，新建独立的 `MultimodalLLMClient`。

**读取配置**：从同样的 `LLMSettings` 读取 URL、模型、认证，但允许覆盖 model（因为多模态可能需要不同模型）。

**核心接口**：

```python
class MultimodalLLMClient:
    """多模态 LLM 调用封装，支持文本 + 图片输入。"""

    def __init__(self, *, model: str | None = None) -> None:
        """初始化。model 为空时使用 LLMSettings 中的默认模型。"""

    async def acall(self, *, text_prompt: str, image_data: bytes, image_content_type: str) -> str:
        """发送一次多模态请求并返回文本结果。

        text_prompt: 提示词文本
        image_data: 图片 bytes
        image_content_type: MIME 类型，如 "image/png"
        """

    async def acall_batch(self, *, text_prompt: str, images: list[tuple[bytes, str]]) -> list[str]:
        """批量发送多模态请求，每张图片独立调用。

        images: list of (image_data, content_type)
        """
```

**请求体构造**：

```python
payload = {
    "max_tokens": self.max_tokens,
    "messages": [{
        "role": "user",
        "content": [
            {"type": "text", "text": text_prompt},
            {"type": "image_url", "image_url": {
                "url": f"data:{content_type};base64,{base64_encoded}",
                "detail": "high"
            }}
        ]
    }],
    "model": self.model,
    ...
}
```

**注意**：
- 超时时间建议比纯文本调用更长（如 300s），因为图片传输和处理更慢
- base64 编码时注意 URL-safe 和 padding
- 批量调用可以用 `asyncio.gather` 并发执行
- 模块导入时创建单例：`multimodal_llm_client = MultimodalLLMClient()`

### 5.4 image_analyzer.py — 图片分析编排

**职责**：接收有效图片列表，调用多模态 LLM 进行分析，生成结构化描述文本。

**策略 B 实现**：图片分析结果不作为独立的 JSON 字段，而是转化为一段结构化的描述文本，后续注入到 `prompts.py` 的提示词中，让 LLM 明确知道这些内容来自图片。

**核心接口**：

```python
IMAGE_ANALYSIS_PROMPT = """
You are analyzing an image embedded in a weekly report email.
Describe the content in a structured, concise manner.
Focus on: progress indicators, charts, tables, risks, and any actionable information.
If the image is a chart/screenshot/table, extract key data points and conclusions.
If the image is decorative or irrelevant, respond with "No meaningful content".
"""

async def analyze_image(image_data: bytes, content_type: str, filename: str) -> ImageAnalysisResult:
    """分析单张图片。"""

async def analyze_images(images: list[ValidImage]) -> list[ImageAnalysisResult]:
    """批量分析有效图片。"""
```

**无图处理**：如果没有有效图片（列表为空），直接返回空列表，不影响主分析流程。

## 6. 与现有流程的集成

### 6.1 service.py 改造点

改造 `analyze_email()` 函数，在 `format_email_for_analysis()` 之前增加图片分析阶段：

```python
async def analyze_email(...):
    # 1. 提取图片
    raw_images = extract_images_from_email(email_data)

    # 2. 过滤图片
    valid_images = filter_images(raw_images)

    # 3. 图片分析（不阻塞主流程，失败不影响文本分析）
    image_descriptions = []
    image_analysis_error = ""
    try:
        results = await analyze_images(valid_images)
        image_descriptions = [r.description for r in results if r.description and r.description != "No meaningful content"]
        image_errors = [r.error for r in results if r.error]
        if image_errors:
            image_analysis_error = "; ".join(image_errors)
    except Exception as exc:
        # 图片分析整体失败不影响文本分析
        image_analysis_error = f"image analysis failed: {exc}"

    # 4. 格式化文本（注入图片描述）
    formatted_content = format_email_for_analysis(email_data, image_descriptions=image_descriptions)

    # 5. 现有三段LLM分析（不变）
    analysis, errors = await process_email_analysis_with_errors(formatted_content, ...)

    # 6. 图片分析错误并入 error 列表
    if image_analysis_error:
        errors.append(image_analysis_error)

    # 7. 构造 payload（不变）
    payload = build_output_payload(email_data, analysis, error="; ".join(errors))
    ...
```

### 6.2 email_parser.py 改造点

#### 6.2.1 `_parse_email()` 增加 inline 图片收集

当前 `_parse_email()` 的 MIME 遍历只把 `Content-Disposition: attachment` 或 `application/octet-stream` 的 part 放入 `attachments` 列表。inline 图片 part（`image/*` 类型、`Content-Disposition: inline` 或无 disposition）被丢弃。

改造方式：在 `for part in message.walk()` 循环中增加对 inline 图片的收集：

```python
# 在现有三类判断（text/plain、text/html、attachment）之后增加
elif content_type.startswith("image/") and "attachment" not in content_disposition:
    content_id = part.get("Content-ID", "").strip("<>")
    decoded_data = part.get_payload(decode=True)
    if decoded_data:
        inline_images.append({
            "content_id": content_id,
            "content_type": content_type,
            "data": decoded_data,
            "size": len(decoded_data),
        })
```

最终 `email_data` 返回值新增 `inline_images` 字段：

```python
return {
    ...,
    "attachments": attachments,
    "inline_images": inline_images,  # 新增
}
```

#### 6.2.2 `format_email_for_analysis()` 增加可选参数

增加可选的 `image_descriptions` 参数：

```python
def format_email_for_analysis(
    email_data: dict[str, Any],
    *,
    image_descriptions: list[str] | None = None,
) -> str:
```

- 如果 `image_descriptions` 非空，在 `[Email Body]` 段落后、`[Attachments]` 段落前插入一个新的 `[Image Content]` 段落
- 每张图片的描述以列表形式呈现，例如：
  ```
  [Image Content]
  - [chart001.png]: Progress chart showing Q3 milestones, 3 of 5 tasks marked complete...
  - [screenshot002.png]: Risk matrix screenshot showing 2 high-priority items...
  ```
- 如果 `image_descriptions` 为空或 None，不插入 `[Image Content]` 段落（完全向后兼容）

### 6.3 prompts.py 改造点

现有的三段提示词（进度抽取、显式风险抽取、隐藏风险抽取）需要微调：

**进度抽取提示词** 增加图片相关内容引导：

```
Rules:
- ...（现有规则不变）
- If [Image Content] section is present, also extract progress information from image descriptions.
- Image descriptions are provided by a vision model; treat them as supplementary to the email text.
```

**显式风险抽取提示词** 增加：

```
- If [Image Content] section is present, also identify risks mentioned in image descriptions.
```

**隐藏风险抽取提示词** 增加：

```
- Consider image descriptions as part of the input when identifying hidden risks.
```

这三段改动都很小，只增加 1-2 行规则，不改变输出 JSON schema。

### 6.4 集成后的数据流

```
email_data
  ├─ extract_images() -> raw_images
  ├─ filter_images() -> valid_images
  ├─ analyze_images() -> image_descriptions (["[chart.png]: ...", ...])
  │
  └─ format_email_for_analysis(email_data, image_descriptions=image_descriptions)
        └─ formatted_content (包含 [Email Body] + [Image Content] + [Attachments])
              └─ process_email_analysis_with_errors(formatted_content)
                    └─ analysis JSON -> build_output_payload -> 写文件 + 同步钉钉
```

## 7. 运行产物目录结构

图片提取阶段**不**保存图片文件到磁盘（这是线 B 的任务）。本次只生成图片描述文本，所有产物都在内存中流转。

运行产物结构不变，仍为：

```text
runs/
  <run_id>/
    run_context.json
    mail/
      risk_json/
        <message_id>.json   # 内容不变，但 errors 字段可能包含图片分析错误
    sync/
      sync_manifest.json
    logs/
```

## 8. 配置与依赖

### 8.1 新增依赖

| 依赖 | 用途 | 是否必需 |
|---|---|---|
| Pillow | 解析图片尺寸（宽高）用于过滤 | 推荐，可用则用，没有则手动解析 PNG/JPEG header |

Pillow 应写入 `requirements-dev.txt`（测试用）和 `requirements.txt`（生产用）。

如果不想引入 Pillow，可以手动解析 PNG/JPEG/GIF 的 header 来提取宽高：
- PNG: 前 33 字节中包含 IHDR 块的宽高
- JPEG: SOF0 段中包含宽高
- GIF: header 中包含逻辑屏幕描述符宽高

### 8.2 环境变量

不需要新增环境变量。多模态 LLM 客户端复用现有的 `LLM_URL`、`LLM_AUTHORIZATION` 等配置。

如果后续需要独立的多模态模型，可在 `MultimodalLLMClient` 构造函数中通过参数传入 model。

## 9. 错误处理与降级

图片分析是**可选增强**，不应阻塞主链路：

| 场景 | 行为 |
|---|---|
| 图片提取失败（如 HTML 解析异常） | 记录日志，`valid_images=[]`，跳过图片分析 |
| 图片过滤后无有效图片 | `valid_images=[]`，跳过图片分析 |
| 多模态 LLM 调用失败（超时/网络错误） | 该图片标记 error，其他图片继续；最终把图片错误并入 errors 字段 |
| 多模态 LLM 返回空或无意义内容 | 该图片的描述文本不注入到 formatted_content 中 |
| 图片分析整体异常 | 捕获异常，把错误信息记录到 errors，文本分析正常执行 |

核心原则：**有图分析图，无图不影响；图分析出错不影响文本分析。**

## 10. 测试策略

### 10.1 image_extractor 测试

测试文件：`tests/test_wrp_image_extractor.py`

| 场景 | 验证点 |
|---|---|
| 纯文本邮件（无图片） | 返回空列表 |
| HTML 中有 base64 data URI 图片 | 正确解码 base64，提取为 base64 origin |
| HTML 中有 cid: 引用 | 找到对应的 MIME inline part 并提取 |
| HTML 中有远程 https 图片 | 不提取（跳过） |
| 附件中的图片 | 不提取（跳过） |
| 混合 cid 和 base64 图片 | 全部正确提取，数量正确 |
| 图片 bytes 内容正确 | data 字段与原始 bytes 一致 |

### 10.2 image_filter 测试

测试文件：`tests/test_wrp_image_filter.py`

| 场景 | 验证点 |
|---|---|
| 空列表输入 | 返回空列表 |
| 小文件（< 500 字节） | 被过滤 |
| 小尺寸图片（宽/高 < 10） | 被过滤 |
| 极端宽高比图片 | 被过滤 |
| 正常图片（尺寸、大小、比例都达标） | 通过过滤 |
| 混合有效和无效图片 | 只返回有效图片 |
| 自定义配置 | 使用自定义 ImageFilterConfig 能调整阈值 |

### 10.3 multimodal_llm 测试

测试文件：`tests/test_wrp_multimodal_llm.py`

| 场景 | 验证点 |
|---|---|
| 正常多模态响应 | 正确提取文本 |
| 空响应 | 抛出 LLMServiceError |
| HTTP 错误 | 抛出 LLMServiceError 并包含状态码 |
| 网络错误 | 抛出 LLMServiceError |
| 非 JSON 响应 | 正确提取文本（不要求 JSON） |
| 批量调用 | 所有图片都能得到响应 |

### 10.4 image_analyzer 测试

测试文件：`tests/test_wrp_image_analyzer.py`

| 场景 | 验证点 |
|---|---|
| 空图片列表 | 返回空列表 |
| 单张图片正常分析 | 返回有效描述 |
| LLM 返回 "No meaningful content" | description 为空或不计入 image_descriptions |
| 分析失败 | error 字段包含错误信息 |
| 批量分析（混合成功/失败） | 成功图片有描述，失败图片有 error |

### 10.5 集成测试

测试文件：在现有 `tests/test_wrp_mail_analysis_service.py` 和 `tests/test_wrp_prompts.py` 中新增测试用例

| 场景 | 验证点 |
|---|---|
| 带图片的邮件分析 | formatted_content 包含 [Image Content] 段落 |
| 不带图片的邮件分析 | formatted_content 不包含 [Image Content] 段落 |
| 图片分析失败 | errors 中包含图片错误，文本分析正常产出 |
| 提示词改造后 | 三段提示词都包含图片相关引导语 |

### 10.6 外部依赖隔离

- `MultimodalLLMClient` 测试使用 monkeypatch 替换 `httpx.AsyncClient.post`
- 图片过滤测试使用伪造的图片 bytes（不需要真实图片文件）
- 不访问真实 LLM 服务

## 11. 阶段划分

本次开发建议按以下顺序推进：

### 阶段 1：基础设施（图片提取 + 过滤）

**文件**：`image_extractor.py`、`image_filter.py`
**测试**：`test_wrp_image_extractor.py`、`test_wrp_image_filter.py`

这个阶段的文件没有外部依赖，容易测试和验证。完成后可以手工运行测试确认。

### 阶段 2：多模态 LLM 客户端

**文件**：`multimodal_llm.py`
**测试**：`test_wrp_multimodal_llm.py`

封装多模态 LLM 调用能力，复用现有 LLM 配置。这个阶段的测试需要 monkeypatch httpx。

### 阶段 3：图片分析编排

**文件**：`image_analyzer.py`
**测试**：`test_wrp_image_analyzer.py`

把图片提取、过滤、LLM 调用串起来，生成图片描述列表。

### 阶段 4：集成到主流程

**改造文件**：
- `email_parser.py` — `format_email_for_analysis()` 增加 `image_descriptions` 参数
- `service.py` — `analyze_email()` 增加图片分析阶段
- `prompts.py` — 三段提示词增加图片引导语

**改造测试**：在现有测试文件中新增用例

这是风险最高的阶段，因为涉及修改现有文件。每一步改动后都应运行全量测试。

## 12. 编码规范与约束

遵循 `docs/ai/project/rules.md` 中已有的所有规则。补充以下针对图片链路的约束：

- **不修改现有 LLMClient**：多模态能力通过独立的 `MultimodalLLMClient` 提供
- **图片分析不阻塞主流程**：任何图片相关异常都不应导致整封邮件分析失败
- **不引入重量级依赖**：如果不引入 Pillow 也能解析图片尺寸，优先不引入
- **保持低耦合**：图片提取、过滤、分析、集成四个模块各自独立，通过明确的数据模型接口通信
- **测试覆盖**：每个新增模块必须有对应的测试文件，图片分析相关的测试必须 monkeypatch 多模态 LLM 调用
- **不改动 artifact JSON 结构**：本次开发只在 errors 字段增加图片相关错误，不新增 top-level key

## 13. 文件变更清单

### 新增文件

| 文件路径 | 说明 |
|---|---|
| `weekly_report_platform/domain/mail_analysis/image_extractor.py` | 图片提取 |
| `weekly_report_platform/domain/mail_analysis/image_filter.py` | 图片过滤 |
| `weekly_report_platform/domain/mail_analysis/multimodal_llm.py` | 多模态 LLM 客户端 |
| `weekly_report_platform/domain/mail_analysis/image_analyzer.py` | 图片分析编排 |
| `tests/test_wrp_image_extractor.py` | 图片提取测试 |
| `tests/test_wrp_image_filter.py` | 图片过滤测试 |
| `tests/test_wrp_multimodal_llm.py` | 多模态 LLM 测试 |
| `tests/test_wrp_image_analyzer.py` | 图片分析测试 |

### 修改文件

| 文件路径 | 改动说明 |
|---|---|
| `weekly_report_platform/domain/mail_analysis/email_parser.py` | `_parse_email()` 增加 inline 图片收集（`inline_images` 字段）；`format_email_for_analysis()` 增加可选 `image_descriptions` 参数 |
| `weekly_report_platform/domain/mail_analysis/service.py` | `analyze_email()` 增加图片分析阶段 |
| `weekly_report_platform/domain/mail_analysis/prompts.py` | 三段提示词各增加 1-2 行图片相关引导语 |
| `requirements.txt` | 新增 Pillow（如果决定引入） |
| `requirements-dev.txt` | 新增 Pillow（如果决定引入） |
| `tests/test_wrp_mail_analysis_service.py` | 新增图片相关集成测试 |
| `tests/test_wrp_prompts.py` | 新增提示词改造测试 |

## 14. 后续任务（线 B：钉钉归档）

本次不实现，但设计上为后续留出接口：

1. 图片文件落盘：在 `extract_images()` 或新增模块中把图片 bytes 写入 `runs/<run_id>/mail/assets/<message_id>/`
2. 分析 JSON 增加 `source.images` 字段，记录图片元信息（path、filename、content_type、size 等）
3. `dingtalk.py` 增加 `attachment` 类型字段支持，通过 MCP `prepare_attachment_upload` 上传并写入
4. 新建独立 profile，灰度验证图片归档链路

## 15. 参考文档

- `README.md` — 项目整体说明
- `docs/ai/project/context.md` — 项目上下文
- `docs/ai/project/rules.md` — 编码规则
- `docs/summarys/MCP_IMAGE_PROBE_SUMMARY.md` — MCP 图片探针实验报告
