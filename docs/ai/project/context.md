# weekly-report-platform 项目上下文

生成日期：2026-05-13

本文档基于当前工作区源码、README、依赖清单、静态前端和测试文件整理。当前目录不是 Git 仓库，无法基于提交历史判断演进背景；涉及生产部署、CI、真实环境参数的部分标记为“待确认”。

## 1. 项目整体目标

`weekly-report-platform` 是一个周报邮件按需抽取、分析与同步平台。它通过 FastAPI 页面发起单次任务，核心目标是：

- 从 POP3 邮箱按本次页面参数筛选周报邮件。
- 解析邮件正文、HTML 正文和附件内容。
- 调用兼容 OpenAI Chat Completions 格式的 LLM 接口，抽取进度、显式风险和隐藏风险。
- 将结构化分析结果按字段映射同步到钉钉多维表。
- 在前端页面展示任务状态、每封邮件处理进度、成功/跳过/失败统计，并支持停止当前任务。

当前产品形态已经移除“持续执行/自动拉取”入口。每次任务都由页面表单提交本次参数启动，不依赖 `.state/automation_config.json`。

## 2. 技术栈

- 语言与运行时：Python 3.10+。
- Web 后端：FastAPI、Uvicorn。
- 前端：原生 HTML、CSS、JavaScript，静态文件由 FastAPI 挂载。
- 配置：`python-dotenv` 读取项目根目录 `.env`。
- HTTP/LLM：`httpx` 异步请求兼容 Chat Completions 的 LLM 服务。
- 邮件：Python 标准库 `poplib`、`email`。
- 附件解析：`markitdown`、`openpyxl`、`python-docx`、`PyMuPDF`。
- 日志：`loguru`。
- 钉钉同步：通过外部 `mcporter` 或 `npx.cmd mcporter` 调用 DingTalk MCP 工具。
- 测试：`pytest`、FastAPI `TestClient`。
- Node.js：项目没有前端构建链；Node 主要用于钉钉 MCP 客户端兜底命令或检查静态 JS 语法。

## 3. 当前目录结构

```text
.
├── README.md
├── requirements.txt
├── requirements-dev.txt
├── docs/
│   ├── ai/project/context.md
│   ├── ai/project/rules.md
│   └── summary/email-extraction-report.md
├── tests/
└── weekly_report_platform/
    ├── api/
    ├── domain/
    │   ├── mail_analysis/
    │   └── run_workspace/
    ├── infrastructure/
    ├── resources/
    ├── web/static/
    ├── automation.py
    ├── dingtalk.py
    ├── processed_mail.py
    └── runtime.py
```

目录职责：

- `weekly_report_platform/api/`：FastAPI 应用、页面和任务 API。
- `weekly_report_platform/domain/mail_analysis/`：邮件抓取、邮件内容格式化、提示词、LLM 调用、分析 artifact 生成。
- `weekly_report_platform/domain/run_workspace/`：每次运行的工作目录创建、清理和上下文落盘。
- `weekly_report_platform/infrastructure/`：环境变量、钉钉配置加载、日志初始化。
- `weekly_report_platform/resources/`：默认钉钉字段和目标表配置。
- `weekly_report_platform/web/static/`：控制台前端静态资源。
- `weekly_report_platform/runtime.py`：后台任务编排器。
- `weekly_report_platform/dingtalk.py`：钉钉同步、字段映射、去重、manifest。
- `weekly_report_platform/processed_mail.py`：本地已处理 `message_id` 状态。
- `weekly_report_platform/automation.py`：保留的自动调度模块；当前 FastAPI 页面/API 已不再挂载该能力，视为未接入的遗留模块。
- `tests/`：以单元测试为主，外部依赖大量使用 monkeypatch 隔离。

## 4. 核心模块说明

### API 层

- `weekly_report_platform/api/app.py`
  - `create_app()` 创建 FastAPI 应用。
  - 挂载 `/static` 并返回 `index.html`。
  - 暴露当前任务接口：
    - `POST /api/tasks`：按本次请求体启动单次抽取任务。
    - `POST /api/tasks/stop`：请求停止当前任务。
    - `GET /api/tasks/{run_id}`：读取任务状态。
  - 当前不再暴露 `/api/automation`、`PUT /api/automation`、`POST /api/automation/stop`。

- `weekly_report_platform/api/schemas.py`
  - 当前保留 `TaskPayload` 类型定义；`POST /api/tasks` 目前直接接收 dict 请求体并交给 `TaskRequest` 归一化。

### 任务编排层

- `weekly_report_platform/runtime.py`
  - `TaskManager` 保证同一时刻只有一个活动任务。
  - 新任务开始前调用 `clear_runs_root()` 清空 `runs/`，当前只保留最近一轮运行产物。
  - `TaskRequest.from_dict()` 归一化本次任务参数：
    - `start_date` 为空时为 `None`，表示不按日期过滤，扫描邮箱历史。
    - `subject_keyword` 为空时默认 `周报`。
    - `max_emails` 为空时为 `None`，表示不限制数量；传入正整数时限制候选邮件数量。
    - `force_refresh` 兼容 bool、`"false"`、`"on"` 等常见页面输入。
  - 后台线程按“拉邮件 -> 分析 -> 同步钉钉”串行处理每封邮件。
  - 使用 `threading.Lock` 保护任务状态，前端通过轮询读取快照。
  - `stop_task()` 是协作式停止，通常在邮件之间或阶段边界生效；不会强杀正在进行的 POP3、LLM 或 mcporter 外部调用。

### 邮件分析层

- `weekly_report_platform/domain/mail_analysis/email_parser.py`
  - `EmailService` 负责 POP3 登录、邮件筛选、邮件头/正文/附件解析。
  - 扫描时先用 `top()` 拉邮件头，命中后再 `retr()` 拉完整邮件。
  - `find_emails_by_filter(start_date, subject_keyword, max_emails)` 支持：
    - `start_date=None`：不按日期过滤。
    - `subject_keyword` 为空：使用服务默认关键字。
    - `max_emails` 为正整数：达到数量后停止扫描。
  - 支持 HTML 转 Markdown、Excel/Word/PDF/TXT 附件解析。

- `weekly_report_platform/domain/mail_analysis/service.py`
  - `fetch_candidate_emails()` 登录邮箱并拉取候选邮件。
  - `analyze_email()` 格式化邮件、调用提示词编排、写 JSON artifact。
  - LLM 抽取阶段返回错误时，`analysis_error` 会带上错误摘要，任务项会标记为“邮件分析失败”，避免继续把带错误的结果写入同步链路。
  - `run_mail_risk_pipeline()` 提供批量分析入口，但当前 Web 任务主流程逐封调用 `analyze_email()`。

- `weekly_report_platform/domain/mail_analysis/prompts.py`
  - 三段 LLM 分析：进度抽取、显式风险抽取、隐藏风险抽取。
  - 进度和显式风险使用 `asyncio.gather()` 并发请求，隐藏风险依赖显式风险结果后再执行。
  - 模型输出必须能解析为严格 JSON object 或 JSON array。

- `weekly_report_platform/domain/mail_analysis/llm_client.py`
  - 读取 `LLM_*` 环境变量。
  - 调用兼容 Chat Completions 的 HTTP 接口。
  - 支持字符串 `message.content` 和 text block 数组两类响应。
  - 网络不可达时错误文案会提示检查 `LLM_URL`、VPN/公司网络和服务可用性。

### 前端页面

- `weekly_report_platform/web/static/index.html`
  - 页面为单次抽取表单，不再包含持续执行配置。
  - 表单字段：
    - `start_date`：开始日期，可选；留空时扫描邮箱历史内容。
    - `subject_keyword`：主题关键字；留空时后端默认 `周报`。
    - `max_emails`：抽取数量，可选；留空时不限制数量。
    - `force_refresh`：是否重新处理历史邮件。
  - “开始抽取”按钮启动任务。
  - “停止当前任务”按钮位于开始按钮下方，调用停止接口。

- `weekly_report_platform/web/static/phase5-app.js`
  - 提交任务时先读取 FormData，再禁用表单控件，避免 disabled 控件丢失表单值。
  - `POST /api/tasks` 请求体直接携带本次页面参数。
  - 每 5 秒轮询 `GET /api/tasks/{run_id}`。
  - `POST /api/tasks/stop` 请求停止当前任务。

### 钉钉同步层

- `weekly_report_platform/dingtalk.py`
  - 入口：`sync_analysis_record()`、`sync_analysis_records()`。
  - 通过 `load_dingtalk_targets()` 读取一个或多个目标表。
  - 用 `mcporter call --http-url ... --tool get_tables/query_records/create_records` 访问 DingTalk MCP。
  - 根据配置中的 `sourcePath` 从分析结果取值，并按字段类型转换为钉钉 cell 值。
  - 同步前检查本地 manifest 和钉钉表内 `message_id`，避免重复写入。
  - 多目标表时，单封邮件只要任一目标失败，聚合结果就是 `failed`；全部成功才是 `synced`。

### 配置与状态

- `weekly_report_platform/infrastructure/config.py`
  - 定义 `PROJECT_ROOT`、默认钉钉配置路径、`.state` 状态路径。
  - 邮箱配置来自 `.env`：`EMAIL_POP3_HOST`、`EMAIL_POP3_PORT`、`EMAIL_USERNAME`、`EMAIL_PASSWORD`、`EMAIL_SUBJECT_KEYWORD`。
  - LLM 配置来自 `.env`：`LLM_URL`、`LLM_MODEL`、`LLM_AUTHORIZATION` 等。
  - 钉钉配置优先级：`DINGTALK_CONFIG_JSON` > `DINGTALK_CONFIG_PATH` > 默认 `resources/config.local.json`。
  - `DINGTALK_MCP_URL` 必填。

- `weekly_report_platform/processed_mail.py`
  - `.state/processed_message_ids.json` 记录已成功处理的邮件。
  - 非 `force_refresh` 时，已处理 `message_id` 会被跳过。

## 5. 关键业务流程

### 单次抽取

1. 用户打开页面，填写本次抽取参数。
2. 前端提交 `POST /api/tasks`，请求体包含 `start_date`、`subject_keyword`、`force_refresh`、`max_emails`。
3. `TaskManager.start_task()` 检查是否已有活动任务；如有则返回 409。
4. 新任务清空 `runs/` 并创建 `runs/<run_id>/` 工作目录。
5. 后台线程加载本地已处理 `message_id` 状态。
6. `fetch_candidate_emails()` 登录 POP3 邮箱并拉取候选邮件。
7. `EmailService.find_emails_by_filter()` 从新到旧扫描邮件：
   - `start_date=None` 时扫描邮箱历史。
   - 有开始日期时过滤早于开始日期的邮件。
   - 主题关键字为空时按 `周报` 筛选。
   - `max_emails` 达到数量后停止。
8. 前端任务列表初始化为候选邮件列表。
9. 每封未跳过邮件进入 LLM 分析，产出 `mail/risk_json/<message_id>.json`。
10. 同步层读取分析 payload，按钉钉配置写入多维表。
11. 成功同步或远端已存在时，`message_id` 写入 `.state/processed_message_ids.json`。
12. 前端每 5 秒轮询 `/api/tasks/{run_id}` 更新状态。

### 停止当前任务

1. 前端点击“停止当前任务”，调用 `POST /api/tasks/stop`。
2. `TaskManager` 设置对应任务的 `stop_event`。
3. 任务在检查停止标记的位置结束，状态变为 `stopped`。
4. 如果正在进行外部 POP3、LLM 或 mcporter 调用，停止不会立即中断该调用；通常会在当前外部调用返回后停止后续处理。

## 6. 主要入口文件

- 后端服务入口：`weekly_report_platform/api/app.py`
  - 命令：`python -m weekly_report_platform.api.app`
  - 默认监听：`127.0.0.1:8000`

- 前端入口：`weekly_report_platform/web/static/index.html`
  - 静态脚本：`weekly_report_platform/web/static/phase5-app.js`
  - 样式：`weekly_report_platform/web/static/phase5-app.css`
  - 当前静态资源版本号：`v=20260513-9`。

- 任务编排入口：`weekly_report_platform/runtime.py::TaskManager.start_task`
- 邮件分析入口：`weekly_report_platform/domain/mail_analysis/service.py::analyze_email`
- 钉钉同步入口：`weekly_report_platform/dingtalk.py::sync_analysis_record`

## 7. 数据流/调用链概览

```text
浏览器页面
  -> POST /api/tasks
  -> TaskManager.start_task()
  -> run_workspace_service.clear_runs_root()
  -> run_workspace_service.create_run_workspace()
  -> mail_analysis.service.fetch_candidate_emails()
  -> EmailService.login_pop3()
  -> EmailService.find_emails_by_filter(start_date, subject_keyword, max_emails)
  -> EmailService.get_email_by_id()
  -> mail_analysis.service.analyze_email()
  -> email_parser.format_email_for_analysis()
  -> prompts.process_email_analysis_with_errors()
  -> llm_client.LLMClient.acall()
  -> mail/risk_json/*.json
  -> dingtalk.sync_analysis_record()
  -> load_dingtalk_targets()
  -> mcporter get_tables/query_records/create_records
  -> sync/sync_manifest.json
  -> .state/processed_message_ids.json
  -> 前端轮询展示任务状态
```

关键数据落点：

- `runs/<run_id>/run_context.json`：运行参数和关键路径。
- `runs/<run_id>/mail/risk_json/*.json`：单封邮件分析结果。
- `runs/<run_id>/sync/sync_manifest.json`：本轮同步结果清单。
- `.state/processed_message_ids.json`：本地已成功处理邮件集合。

## 8. 常用命令与接口

环境初始化：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

启动后端和页面：

```powershell
python -m weekly_report_platform.api.app
```

运行测试：

```powershell
python -m pytest -q --import-mode=importlib
```

关键访问地址：

- 页面：`http://127.0.0.1:8000/`
- 启动任务：`POST /api/tasks`
- 停止当前任务：`POST /api/tasks/stop`
- 查询任务状态：`GET /api/tasks/{run_id}`

当前已移除：

- `GET /api/automation`
- `PUT /api/automation`
- `POST /api/automation/stop`

## 9. 测试方式

项目使用 `pytest`。推荐命令：

```powershell
python -m pytest -q --import-mode=importlib
```

当前测试特点：

- `tests/conftest.py` 会把项目根目录加入 `sys.path`。
- 临时文件使用项目根下 `.tmp_testdata/tmp_<uuid>`，测试结束后清理。
- 外部系统通常通过 `monkeypatch` 替换，不直接访问真实 POP3、LLM 或钉钉。
- API 测试使用 FastAPI `TestClient`。
- 覆盖重点包括：
  - API 路由、任务冲突、停止任务、已移除 automation API。
  - 单次任务参数解析：空开始日期、默认关键字、抽取数量、强制刷新。
  - 邮件头、正文、附件解析。
  - 邮件筛选中的日期过滤、数量限制和全历史扫描。
  - LLM 响应文本抽取和 JSON 解析约束。
  - 钉钉字段映射、去重、manifest、多个 target 聚合结果。
  - run 工作目录创建与清理。
  - 任务编排中的跳过、失败、停止路径。

最近一次验证：`55 passed`。

## 10. 构建/启动方式

此项目没有发现 `pyproject.toml`、`setup.py`、`package.json` 或前端构建脚本。当前启动方式是直接运行 Python 模块：

```powershell
python -m weekly_report_platform.api.app
```

启动前需要确保：

- Python 依赖已安装。
- 项目根目录 `.env` 存在且包含邮箱、LLM、钉钉 MCP 配置。
- 系统可用 `mcporter`，或可通过 `npx.cmd mcporter` 调用。
- Node.js 可用，尤其是依赖 `npx.cmd mcporter` 兜底路径时。

部署方式、服务进程管理、反向代理、生产认证方式：待确认。

## 11. 当前项目中的重要约定

- 系统按需启动单次抽取任务，不再通过页面/API 维护持续执行配置。
- `POST /api/tasks` 的请求体就是本次执行参数。
- `start_date` 为空表示扫描邮箱历史；有值时从该日期开始筛选。
- `subject_keyword` 为空表示默认 `周报`。
- `max_emails` 为空表示不限制数量；传入正整数时按数量抽取。
- 同一时间只允许一个活动任务；任务运行中无法启动新任务。
- 前端提供“停止当前任务”按钮，停止语义是协作式停止。
- 新任务启动前会清空 `runs/`，当前实现只保留最近一轮运行产物。
- 非 `force_refresh` 时，本地 `.state/processed_message_ids.json` 中已有的邮件会跳过。
- 钉钉同步会先查本地 manifest，再查远端表内 `message_id`，避免重复写入。
- 分析结果中的 `errors` 非空时，同步层会把该邮件标记为跳过，不写入钉钉。
- LLM 抽取错误会在分析阶段转成 `analysis_error`，任务项标记为失败。
- 钉钉字段映射依赖配置中的 `fieldName`、`type`、`required`、`sourcePath`。
- `sourcePath` 使用点号路径访问分析结果，例如 `analysis.进度抽取结果.分析结果`。
- `DINGTALK_CONFIG_JSON` 优先级高于 `DINGTALK_CONFIG_PATH` 和默认配置文件。
- LLM 输出必须是严格 JSON；提示词 key 与钉钉字段 `sourcePath` 强耦合。
- 前端无构建步骤，静态资源版本通过 URL query 参数手动控制。
- 日志输出到 stderr，`runs/<run_id>/logs/` 当前只创建目录，是否实际写入日志文件：待确认。

## 12. 潜在高风险区域

- `run_workspace.service.clear_runs_root()` 会删除 `runs_root` 下所有子项；如路径计算错误，破坏性较高。
- `dingtalk.py` 会真实调用外部 `mcporter create_records` 写入钉钉，多次测试真实环境可能产生重复业务数据。
- `message_id` 是本地去重和远端去重的核心字段，缺失或格式变化会影响跳过/同步逻辑。
- `prompts.py` 中 JSON schema key 与 `config.local.json` 的 `sourcePath` 强相关，任意改名都会影响钉钉字段取值。
- `email_parser.py` 处理邮件编码、HTML、附件和临时文件，边界多，容易出现中文编码、文件格式和内存占用问题。
- POP3 连接当前使用 SSL；如果出现 `UNEXPECTED_EOF_WHILE_READING`，常见原因包括 VPN/公司网络不可达、POP3 端口与 SSL 模式不匹配、服务临时拒绝连接。
- `runtime.py` 使用线程和锁维护状态，修改时需格外注意竞态、死锁、停止语义和前端轮询一致性。
- `LLMClient` 和 `EmailService` 在模块导入时创建单例，环境变量加载和测试 monkeypatch 顺序容易受影响。
- POP3、LLM、mcporter 都是外部慢调用，当前任务停止不能强制中断正在执行的外部请求。
- `.env` 可能包含敏感凭据，不应写入文档、日志或测试快照。
- 当前 FastAPI 页面/API 未看到认证鉴权，是否只在可信内网运行：待确认。

## 13. 不建议随意修改的模块

- `weekly_report_platform/dingtalk.py`
  - 涉及真实写表、字段映射、去重、manifest 和多 target 聚合。

- `weekly_report_platform/runtime.py`
  - 核心任务状态机、后台线程、停止语义和前端轮询数据结构都在这里。

- `weekly_report_platform/domain/mail_analysis/prompts.py`
  - 输出 schema 与下游 `sourcePath` 强耦合。

- `weekly_report_platform/domain/mail_analysis/email_parser.py`
  - 邮件协议、编码和附件解析复杂，回归面大。

- `weekly_report_platform/domain/run_workspace/service.py`
  - 包含清理 `runs/` 的文件系统操作。

- `weekly_report_platform/infrastructure/config.py`
  - 环境变量优先级、默认路径和钉钉 target 结构会影响全链路。

- `weekly_report_platform/web/static/phase5-app.js`
  - 前端状态锁定、表单取值、停止按钮、轮询、冲突处理与 API 返回结构紧密绑定。

- `weekly_report_platform/automation.py`
  - 当前为未接入 FastAPI 页面/API 的遗留自动调度模块；如要恢复自动能力，需要重新评估产品需求、API、前端和测试。

## 14. 后续 AI Coding 需要特别注意的地方

- 改动前先确认是否会触发真实外部副作用；涉及 POP3、LLM、钉钉时优先使用 monkeypatch 或 stub 测试。
- 不要读取或暴露 `.env` 中的真实凭据。
- 修改任务入参时，同步检查：
  - `runtime.py::TaskRequest`。
  - `service.py::fetch_candidate_emails()`。
  - `email_parser.py::find_emails_by_filter()`。
  - 前端 `phase5-app.js` 的 FormData 取值顺序。
  - API 和前端测试。
- 修改提示词或分析 JSON 结构时，同步更新：
  - `prompts.py` 的 key 常量。
  - `resources/config.local.json` 的 `sourcePath`。
  - `dingtalk.py` 中特殊字段格式化逻辑。
  - 相关测试。
- 修改任务状态或 API 返回结构时，同步检查前端 `phase5-app.js` 的状态枚举、按钮禁用、停止按钮和轮询逻辑。
- 修改 run 工作区逻辑时，重点保护 `clear_runs_root()` 的路径边界，避免误删。
- 修改钉钉同步时，要覆盖以下测试场景：字段缺失、远端已存在、本地 manifest 已同步、多 target、某个 target 失败、mcporter 返回非 JSON。
- 修改邮件解析时，要补充中文编码、HTML、Excel、Word、PDF、TXT 和不支持附件类型的回归测试。
- 当前没有看到 CI 配置、打包配置和生产部署配置；引入新命令或依赖时要同时更新 README 和本上下文文档。
- 当前目录不是 Git 仓库；如需做版本比较、提交或分支操作，需先确认真实仓库位置：待确认。
