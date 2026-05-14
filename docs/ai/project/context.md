# weekly-report-platform 项目上下文

更新日期：2026-05-14

## 1. 项目目标

从 POP3 邮箱按页面参数拉取周报邮件，解析正文/附件/内嵌图片，调用 LLM 抽取结构化结果（进度、显式风险、隐藏风险），同步到钉钉多维表，通过 FastAPI 页面管理单次任务。

## 2. 技术栈

- Python 3.10+，FastAPI + Uvicorn
- 前端：原生 HTML/CSS/JS，静态文件由 FastAPI 挂载
- 配置：python-dotenv 读取项目根 `.env`
- HTTP/LLM：httpx 异步请求，兼容 OpenAI Chat Completions 格式
- 邮件：poplib + email 标准库
- 附件解析：markitdown、openpyxl、python-docx、PyMuPDF
- 图片尺寸解析：手动解析 PNG/JPEG/GIF header（不依赖 Pillow）
- 日志：loguru
- 钉钉同步：mcporter / npx.cmd mcporter 调用 DingTalk MCP
- 测试：pytest + FastAPI TestClient

## 3. 项目结构

```text
weekly_report_platform/
├── api/              # FastAPI 应用与路由
├── domain/
│   ├── mail_analysis/    # 邮件抓取、LLM 调用、提示词、分析、图片链路
│   └── run_workspace/    # 运行工作目录管理
├── infrastructure/   # 配置加载（MailSettings/LLMSettings/VisionSettings）、日志
├── resources/        # 钉钉字段配置
├── web/static/       # 前端静态资源
├── runtime.py        # 任务编排（TaskManager）
├── dingtalk.py       # 钉钉同步
├── processed_mail.py # 已处理邮件去重
└── automation.py     # 遗留模块，未接入
```

## 4. 核心模块

### API 层 — `api/app.py`

- `POST /api/tasks` — 按本次参数启动单次抽取
- `POST /api/tasks/stop` — 停止当前任务
- `GET /api/tasks/{run_id}` — 查询任务状态

### 任务编排 — `runtime.py`

- `TaskManager` 保证同一时刻只有一个活动任务
- 新任务清空 `runs/`，只保留最近一轮产物
- `TaskRequest.from_dict()` 归一化参数：`start_date`（空=全历史）、`subject_keyword`（空=周报）、`max_emails`（空=不限）、`force_refresh`
- 后台线程串行处理：拉邮件 → 分析 → 同步钉钉
- 停止是协作式，不强杀 POP3/LLM/mcporter 外部调用

### 邮件分析层 — `domain/mail_analysis/`

| 模块 | 职责 |
|---|---|
| `email_parser.py` | POP3 登录、邮件筛选、正文/附件/inline 图片解析、`format_email_for_analysis()` |
| `service.py` | `analyze_email()` 编排：提取图片 → 过滤 → vision 分析 → 注入描述 → 三段 LLM → 写 JSON |
| `prompts.py` | 三段 LLM 提示词（进度、显式风险、隐藏风险），含图片引导语 |
| `llm_client.py` | HundSun 内部 LLM 调用，content 为纯字符串 |
| `image_extractor.py` | 从 email_data 提取 cid 引用和 base64 data URI 内嵌图片 |
| `image_filter.py` | 启发式过滤：大小<5KB、尺寸<10px、宽高比>100 的图片剔除 |
| `multimodal_llm.py` | 外部 vision 网关调用（VisionSettings），OpenAI content 数组格式 |
| `image_analyzer.py` | 接收有效图片 → 调用 vision LLM → 返回 ImageAnalysisResult；"No meaningful content" 描述置空 |

### 钉钉同步 — `dingtalk.py`

- `sync_analysis_record()` / `sync_analysis_records()` 入口
- mcporter 调用 DingTalk MCP：get_tables / query_records / create_records
- 配置 `sourcePath` 点号路径取值，`analysis.进度抽取结果.分析结果` 等
- 本地 manifest + 远端 `message_id` 双重去重
- 多 target 时单封邮件任一失败即标记 `failed`

### 配置 — `infrastructure/config.py`

| 配置类 | 环境变量 | 用途 |
|---|---|---|
| `MailSettings` | `EMAIL_POP3_*` | POP3 邮箱连接 |
| `LLMSettings` | `LLM_*` | HundSun 内部文本 LLM（只接受纯字符串 content） |
| `VisionSettings` | `VISION_*` | 外部 vision LLM（支持 OpenAI content 数组） |

VisionSettings 环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `VISION_URL` | 空（必填） | 外部 vision API 端点 |
| `VISION_MODEL` | `qwen3.6-plus` | vision 模型名称 |
| `VISION_AUTHORIZATION` | 空（必填） | 认证信息 |
| `VISION_MAX_TOKENS` | `8000` | 最大输出 token |
| `VISION_TEMPERATURE` | `0.01` | 温度参数 |
| `VISION_TIMEOUT` | `300.0` | 请求超时（秒） |

钉钉配置优先级：`DINGTALK_CONFIG_JSON` > `DINGTALK_CONFIG_PATH` > 默认 `resources/config.local.json`。

## 5. 图片链路

### 数据流

```text
email_data
  → image_extractor.extract_images_from_email()     提取 cid/base64 图片
  → image_filter.filter_images()                     启发式过滤（<5KB, <10px, 宽高比>100）
  → image_analyzer.analyze_images()                  外部 vision LLM 分析
  → image_descriptions（description 非空才注入）
  → format_email_for_analysis(image_descriptions=...) 插入 [Image Content] 段落
  → process_email_analysis_with_errors()             三段文本 LLM
```

### 错误隔离

图片分析整体被 try/except 包裹，任何阶段失败（提取异常、过滤无图、vision 调用失败、未配置 VISION_URL）都不阻塞文本分析。失败时 `image_descriptions` 为空列表，`format_email_for_analysis()` 不插入 `[Image Content]` 段落。

### 双 LLM 通道

| 通道 | 端点 | content 格式 | 用途 |
|---|---|---|---|
| 文本 LLM（LLMSettings） | HundSun 内部网关 | 纯字符串 | 进度/风险/隐藏风险分析 |
| Vision LLM（VisionSettings） | 外部 OpenAI-compatible 网关 | content 数组（text + image_url） | 图片 → 描述文本 |

原因：HundSun 内部 Java 包装层只接受 `messages[].content` 为纯字符串，不支持 OpenAI 风格 content 数组格式。

### 待验证

- 邮件周报正文内嵌图片的 vision 抽取效果（图片识别准确率、描述文本质量、对三段 LLM 分析结果的实际影响）尚未基于真实邮件验证。当前仅通过 monkeypatch 单元测试验证链路通顺。

### 线B规划（钉钉图片归档）

后续开发，筛选策略：

```text
启发式过滤（5KB） → vision LLM 语义判断 → 只归档 description 非空的图片
```

还需补充 `ImageFilterConfig.max_file_size` 上限（约 10MB），防止超大图片上传钉钉失败。

## 6. 业务流程

1. 页面提交参数 → `POST /api/tasks`
2. `TaskManager` 检查无活动任务后启动，清空 `runs/`，创建工作目录
3. `fetch_candidate_emails()` 登录 POP3 拉取候选邮件
4. 逐封邮件：图片分析 → 文本分析 → 产出 `risk_json/<message_id>.json`
5. 钉钉同步：字段映射 + 去重 + 写多维表
6. 成功后 `message_id` 写入 `.state/processed_message_ids.json`
7. 前端每 5 秒轮询更新状态

## 7. 运行产物

```text
runs/<run_id>/
  run_context.json
  mail/risk_json/<message_id>.json
  sync/sync_manifest.json
  logs/
```

本地状态：`.state/processed_message_ids.json`

## 8. 关键约定

- 同一时间只有一个活动任务
- `start_date` 空=全历史，`subject_keyword` 空=周报，`max_emails` 空=不限
- 非 `force_refresh` 时本地已处理邮件跳过
- 分析结果 `errors` 非空时同步层跳过该邮件
- LLM 输出必须是严格 JSON，提示词 key 与钉钉 `sourcePath` 强耦合
- 图片分析不阻塞主流程，`VISION_URL` 未配置时静默跳过
- `format_email_for_analysis()` 的 `image_descriptions` 参数向后兼容
- `MultimodalLLMClient` 使用独立 VisionSettings，不修改现有 `llm_client.py`
- 钉钉配置优先级：`DINGTALK_CONFIG_JSON` > `DINGTALK_CONFIG_PATH` > 默认文件
- 前端无构建步骤，资源版本通过 URL query 参数控制

## 9. 测试

```powershell
python -m pytest -q --import-mode=importlib
```

- 外部系统通过 monkeypatch 隔离（POP3、LLM、vision LLM、钉钉）
- API 测试使用 FastAPI TestClient
- 图片模块测试：提取（cid/base64/远程跳过/附件跳过）、过滤（大小/尺寸/宽高比/自定义配置）、vision LLM（正常响应/HTTP 错误/网络错误/批量/部分失败）、分析编排（空列表/正常/无意义内容/失败/批量混合）
- 集成测试：`format_email_for_analysis` 的 image_descriptions 注入/不注入、提示词图片引导语

最近验证：108 passed

## 10. 常用命令

```powershell
# 环境初始化
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

# 启动后端（默认 http://127.0.0.1:8000）
python -m weekly_report_platform.api.app

# 运行测试
python -m pytest -q --import-mode=importlib
```

## 11. 需注意的模块

| 模块 | 原因 |
|---|---|
| `runtime.py` | 任务状态机、线程、锁、停止语义 |
| `dingtalk.py` | 真实写表、字段映射、去重、多 target 聚合 |
| `prompts.py` | 输出 schema 与钉钉 `sourcePath` 强耦合，含图片引导语 |
| `multimodal_llm.py` | payload 结构需与外部 vision 网关兼容 |
| `email_parser.py` | 邮件编码、HTML、附件、inline 图片、临时文件 |
| `infrastructure/config.py` | 环境变量优先级影响全链路 |

## 12. 修改时需同步检查

- 改任务入参：`runtime.py` TaskRequest + `service.py` + `email_parser.py` + 前端 JS + API 测试
- 改提示词/JSON 结构：`prompts.py` key 常量 + `config.local.json` sourcePath + `dingtalk.py` 格式化 + 测试
- 改任务状态/API 返回：前端 JS 状态枚举 + 按钮 + 轮询
- 改钉钉同步：字段缺失、远端已存在、本地 manifest、多 target、mcporter 非 JSON
- 改邮件解析：中文编码、HTML、各附件类型回归
- 改图片链路：`image_extractor` + `image_filter` + `multimodal_llm` + `image_analyzer` + `service.py` + `prompts.py` + 集成测试