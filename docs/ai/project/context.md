# weekly-report-platform 项目上下文

生成日期：2026-05-14
更新日期：2026-05-15

## 1. 项目目标

周报邮件按需抽取、分析与同步平台。通过 FastAPI 页面发起单次任务：

- 从 POP3 邮箱按本次参数筛选周报邮件。
- 解析邮件正文、HTML 正文和附件内容。
- 调用 LLM 抽取进度和风险信息。
- 将结构化结果按字段映射同步到钉钉多维表。
- 前端展示任务状态和统计，支持停止当前任务。

## 2. 技术栈

Python 3.10+、FastAPI、Uvicorn、httpx（LLM）、poplib（邮件）、markitdown/openpyxl/python-docx/PyMuPDF（附件）、loguru（日志）、pytest（测试）。钉钉同步通过 mcporter 调用 DingTalk MCP。

## 3. 目录结构

```text
.
├── docs/
│   ├── ai/project/          # AI 项目上下文和规则
│   └── summary/             # 功能报告
├── tests/
└── weekly_report_platform/
    ├── api/                  # FastAPI 应用和 API
    ├── domain/mail_analysis/ # 邮件抓取、格式化、提示词、LLM 调用
    ├── domain/run_workspace/ # 运行工作目录管理
    ├── infrastructure/       # 配置加载、日志
    ├── resources/            # 默认钉钉字段和目标表配置
    ├── web/static/           # 前端静态资源
    ├── dingtalk.py           # 钉钉同步、字段映射、去重
    ├── processed_mail.py     # 已处理邮件状态
    └── runtime.py            # 后台任务编排
```

## 4. 核心模块

### API 层 (`api/app.py`)

- `POST /api/tasks`：启动单次抽取任务
- `POST /api/tasks/stop`：停止当前任务
- `GET /api/tasks/{run_id}`：读取任务状态

### 邮件分析层 (`domain/mail_analysis/`)

- `email_parser.py`：POP3 登录、邮件筛选、MIME 解析、附件格式化
- `prompts.py`：两段 LLM 分析（进度抽取 + 风险抽取，`asyncio.gather` 并发），结果编排和错误收集
- `llm_client.py`：兼容 OpenAI Chat Completions 的 HTTP 调用
- `service.py`：单封邮件分析编排、artifact 构造与落盘

### 钉钉同步层 (`dingtalk.py`)

- 入口：`sync_analysis_record()`、`sync_analysis_records()`
- 字段类型处理：`text`（字符串化）、`number`、`progress`（浮点数值）、`date`（截取 YYYY-MM-DD）
- 风险详情按 6 列顺序格式化：风险类型、风险等级、风险描述、风险措施和最新进展、风险责任人、计划解决日期
- 同步前检查本地 manifest 和钉钉表内 `message_id`，避免重复写入

### 配置 (`infrastructure/config.py`)

- 钉钉配置优先级：`DINGTALK_CONFIG_JSON` > `DINGTALK_CONFIG_PATH` > 默认 `resources/config.local.json`
- `DINGTALK_MCP_URL` 必填
- `build_default_fields()` 返回 11 个字段定义

### 任务编排 (`runtime.py`)

- `TaskManager` 保证同一时刻只有一个活动任务
- 新任务开始前清空 `runs/`，只保留最近一轮产物
- 协作式停止，在邮件之间或阶段边界生效

## 5. 数据流

```text
浏览器 -> POST /api/tasks
  -> TaskManager.start_task()
  -> fetch_candidate_emails()
  -> EmailService (POP3 筛选、MIME 解析)
  -> format_email_for_analysis()
  -> process_email_analysis_with_errors()（进度 + 风险并发抽取）
  -> mail/risk_json/*.json
  -> dingtalk.sync_analysis_record()
  -> mcporter create_records
  -> .state/processed_message_ids.json
  -> 前端轮询展示状态
```

数据落点：
- `runs/<run_id>/run_context.json`：运行参数
- `runs/<run_id>/mail/risk_json/*.json`：分析结果
- `runs/<run_id>/sync/sync_manifest.json`：同步清单
- `.state/processed_message_ids.json`：已处理邮件集合

## 6. 钉钉字段映射

当前 `config.local.json` 定义 11 个字段，目标表 `XD9FUh1`（baseId: `ZX6GRezwJl7DbxOxirwj3wGzVdqbropQ`）：

| 业务 key | 钉钉字段名 | 类型 | sourcePath |
|---|---|---|---|
| `message_id` | 邮件标识 | text | `message_id` |
| `project_name` | 项目名称 | text | `analysis.进度抽取结果.项目名称` |
| `project_code` | 项目编号 | text | `analysis.进度抽取结果.项目编号` |
| `project_cycle` | 项目周报周期 | text | `analysis.进度抽取结果.项目周报周期` |
| `project_progress` | 项目进度 | progress | `analysis.进度抽取结果.项目进度` |
| `milestone` | 里程碑 | text | `analysis.进度抽取结果.里程碑` |
| `risk_details` | 风险详情 | text | `analysis.风险抽取结果.风险详情` |
| `overall_progress` | 项目整体进展 | text | `analysis.进度抽取结果.项目整体进展` |
| `weekly_summary` | 本周小结 | text | `analysis.进度抽取结果.本周小结` |
| `next_week_plan` | 下周计划 | text | `analysis.进度抽取结果.下周计划` |
| `extract_date` | 邮箱抽取日期 | date | `processed_at` |

## 7. LLM 输出结构

进度抽取返回扁平 JSON object（无嵌套 `分析结果` 对象）：
- `原文抽取`：原文片段数组
- `一致性分析`：邮件正文与附件内容的一致性对比
- `项目整体进展`：Excel 周报附件中"项目整体进展概况"原文，保持段落分隔可读性
- `项目名称`、`项目编号`、`项目周报周期`、`项目进度`（0~1 浮点数）、`里程碑`、`本周小结`、`下周计划`

风险抽取返回 JSON object，包含：
- `原文抽取`、`一致性分析`
- `风险详情`：数组，每条含 序号、风险类型、风险等级、风险描述、风险措施和最新进展、风险责任人、计划解决日期

## 8. 常用命令

```powershell
# 启动服务
python -m weekly_report_platform.api.app

# 运行测试
python -m pytest -q --import-mode=importlib

# 当前测试基线：58 passed
```

关键地址：页面 `http://127.0.0.1:8000/`，任务 API `/api/tasks`。

## 9. 已知风险与注意事项

- `clear_runs_root()` 会删除 `runs/` 下所有子项，路径计算错误破坏性高
- `dingtalk.py` 会真实写钉钉表，测试时必须 monkeypatch
- `message_id` 是本地和远端去重核心键，缺失或格式变化影响跳过/同步
- LLM 输出 JSON key 与 config `sourcePath` 强耦合，改任一方需同步更新
- POP3、LLM、mcporter 均为外部慢调用，停止不能立即中断
- `.env` 含敏感凭据，不应写入文档或日志
- 当前无认证鉴权，需确认是否只在可信内网运行
