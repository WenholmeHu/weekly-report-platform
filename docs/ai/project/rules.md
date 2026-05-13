# weekly-report-platform AI Coding 项目规则

生成日期：2026-05-13

本文档是本项目后续 AI Coding 的长期约束。执行任何代码修改前，应先阅读 `docs/ai/project/context.md` 和本文档，并以现有实现、测试和业务约定为准。

## 1. 代码风格约定

- Python 代码使用 Python 3.10+ 风格，优先保留现有模块的写法。
- 新增函数、类、数据结构应补充类型标注；现有代码大量使用 `dataclass` 和 `Path`，新增逻辑应优先沿用。
- 业务状态、配置对象、任务结果等结构化数据优先使用 `dataclass`、Pydantic model 或明确的 dict schema，不要随意引入松散字符串拼接。
- 文件读写统一显式指定 `encoding="utf-8"`，JSON 写入使用 `ensure_ascii=False` 和可读缩进，除非现有调用链另有约定。
- 异步逻辑只在确有外部 I/O 或并发收益时使用；不要把同步状态机强行改成异步架构。
- 日志统一使用 `weekly_report_platform.infrastructure.logging.logger`，不要混用 `print()` 输出业务日志。
- 注释应解释业务规则、边界条件或外部系统约束；不要添加“代码在做什么”的空泛注释。
- 前端静态代码保持原生 HTML/CSS/JavaScript 方案，不引入构建链，除非项目明确升级前端技术栈。
- 错误信息应可诊断，不要吞掉异常；面向页面展示的错误文案应保持简短、稳定。

## 2. 命名规范

- Python 模块、函数、变量使用 `snake_case`。
- 类名使用 `PascalCase`，例如 `TaskManager`、`TaskState`。
- 常量使用 `UPPER_SNAKE_CASE`，尤其是提示词 key、字段路径、状态集合等跨模块约定。
- 测试函数使用 `test_` 前缀；当前项目多数测试采用 `test_wrp_...` 命名，新增同类测试优先沿用该风格。
- API 路径保持 REST 风格和当前前缀：任务相关使用 `/api/tasks...`。当前页面/API 不再暴露 `/api/automation...`。
- 状态值必须保持小写英文字符串，例如 `pending`、`running`、`synced`、`skipped`、`failed`；新增状态必须同步前端映射和测试。
- 钉钉字段配置中的业务 key 应保持语义稳定，例如 `message_id`、`subject`、`progress_analysis`。

## 3. 目录放置规则

- API 路由和请求/响应模型放在 `weekly_report_platform/api/`。
- 业务领域逻辑放在 `weekly_report_platform/domain/` 下对应子域：
  - 邮件抓取、附件解析、LLM 分析放在 `domain/mail_analysis/`。
  - run 工作目录管理放在 `domain/run_workspace/`。
- 配置、日志等基础设施能力放在 `weekly_report_platform/infrastructure/`。
- 钉钉同步当前集中在 `weekly_report_platform/dingtalk.py`；除非有明确重构计划，不要拆散该模块的外部入口。
- 任务编排放在 `weekly_report_platform/runtime.py`。
- `weekly_report_platform/automation.py` 当前是未接入 FastAPI 页面/API 的遗留自动调度模块；不要在没有明确需求和测试计划时恢复或依赖它。
- 前端静态资源只放在 `weekly_report_platform/web/static/`。
- 默认配置文件放在 `weekly_report_platform/resources/`。
- 测试文件放在 `tests/`，命名应能对应被测模块或业务能力。
- AI 文档放在 `docs/ai/project/`，不要混入运行时代码目录。
- 运行产物只应写入 `runs/`、`.state/` 或测试临时目录，不要把产物写入源码目录。

## 4. 依赖使用规则

- 新依赖必须有明确必要性，优先使用标准库和项目已有依赖。
- Python 运行依赖写入 `requirements.txt`，测试或开发依赖写入 `requirements-dev.txt`。
- 不要引入重量级框架替代当前轻量实现，例如为了小范围前端交互引入 React/Vue 构建链。
- 外部服务调用优先封装在边界模块内，例如 LLM 调用集中在 `llm_client.py`，钉钉 MCP 调用集中在 `dingtalk.py`。
- 不要在业务代码中直接读取或硬编码敏感凭据；配置必须来自 `.env` 或已有配置加载机制。
- 引入会触发网络、文件系统删除、外部写入的依赖或命令时，必须补充隔离测试和文档说明。
- `mcporter`、POP3、LLM 均视为外部副作用边界；测试中不得直接访问真实服务。

## 5. 测试规则

- 修改业务逻辑必须补充或更新对应测试，不能只依赖手工验证。
- 默认测试命令：

```powershell
python -m pytest -q --import-mode=importlib
```

- API 行为变更应覆盖 `TestClient` 测试，包括成功路径、冲突路径、停止任务路径和已移除路由的行为。
- 邮件解析变更应覆盖中文编码、HTML 正文、附件解析和不支持附件类型。
- LLM 相关变更应使用 monkeypatch/stub，不请求真实模型；必须覆盖空响应、非 JSON、非 object/array 等失败路径。
- 钉钉同步变更必须覆盖本地 manifest 去重、远端 message_id 去重、多 target 聚合、字段缺失、mcporter 失败和无效 JSON。
- 任务编排变更必须覆盖单任务锁、停止语义、运行中冲突和单次任务参数解析。
- 文件系统变更必须使用测试临时目录，禁止测试直接清理真实项目产物目录以外的位置。
- 若因环境限制无法运行完整测试，必须在最终说明中明确列出未运行的测试和原因。

## 6. 禁止随意修改的内容

- 禁止随意修改 `.env` 或读取、输出其中真实敏感信息。
- 禁止随意修改 `weekly_report_platform/resources/config.local.json` 中真实 `baseId`、`tableId` 和字段映射。
- 禁止随意改变 LLM 输出 JSON key，尤其是 `进度抽取结果`、`风险抽取结果`、`隐藏风险` 等下游依赖字段。
- 禁止随意改变 `message_id` 的提取、清洗和去重语义。
- 禁止随意改变 `runs/` 新任务前清理策略，除非同时评估产物保留、磁盘占用和误删风险。
- 禁止绕过“同一时刻只允许一个活动任务”的限制。
- 禁止重新引入持续执行/自动拉取页面或 `/api/automation...` 路由，除非这是明确需求，并同步更新 README、`docs/ai/project/` 和测试。
- 禁止让测试访问真实 POP3、LLM、钉钉 MCP 或真实钉钉表。
- 禁止为局部需求大规模重写前端、任务编排、同步层或配置加载机制。
- 禁止提交 `.venv/`、`__pycache__/`、`.tmp_testdata/`、`runs/`、`.state/` 等生成内容。

## 7. 公共模块修改规范

- 修改 `runtime.py` 时，必须评估任务状态机、线程锁、停止语义、前端轮询结构和本地 processed state 的影响。
- 修改单次任务入参时，必须同步检查 `runtime.py::TaskRequest`、`service.py::fetch_candidate_emails()`、`email_parser.py::find_emails_by_filter()`、前端 FormData 取值和 API 测试。
- 修改 `automation.py` 时，应先确认是否仍有接入点；当前 FastAPI 页面/API 不使用该模块。
- 修改 `dingtalk.py` 时，必须评估真实写表副作用、字段映射、sourcePath、manifest、远端去重和多 target 聚合。
- 修改 `prompts.py` 时，必须同步检查钉钉配置和文本格式化逻辑；任何 schema 变化都需要测试覆盖。
- 修改 `email_parser.py` 时，必须覆盖编码、MIME、HTML、附件、临时文件清理和大附件性能风险。
- 修改 `config.py` 时，必须保持环境变量优先级清晰，避免破坏 `.env`、`DINGTALK_CONFIG_JSON`、`DINGTALK_CONFIG_PATH` 的兼容性。
- 修改 `phase5-app.js` 时，必须同步验证 API 响应结构、按钮禁用逻辑、轮询停止条件和状态标签。
- 修改 `phase5-app.js` 中表单提交逻辑时，必须确保在禁用 input 前读取 FormData；disabled 控件不会进入 FormData。
- 公共模块新增能力应尽量向后兼容；确需破坏兼容时，必须更新测试和文档。

## 8. 新增文件规范

- 新增 Python 源码文件必须位于最贴近职责的包目录，并提供清晰模块职责。
- 新增测试文件应放在 `tests/`，命名为 `test_*.py`，优先与被测模块同名或同业务能力对应。
- 新增配置样例应避免真实敏感信息；真实本地配置只应放在 `.env` 或受控的本地文件中。
- 新增文档应放在 `docs/` 下合适目录；AI 项目上下文和规则文档放在 `docs/ai/project/`。
- 新增运行产物目录必须能被安全清理，不应与源码、配置、文档混放。
- 新增静态资源应放在 `weekly_report_platform/web/static/`，并检查 `index.html` 引用路径。
- 新增文件应使用 UTF-8；中文业务文案允许保留中文，不需要强行转义。

## 9. 重构限制

- 不为“看起来更整洁”做无业务收益的大规模重构。
- 不在同一变更中混合重构和行为修改；若无法避免，必须拆分说明并增加测试。
- 不改变公共入口函数签名，除非已同步所有调用方、测试和文档。
- 不把当前单进程、线程式任务模型改成队列、数据库或分布式架构，除非这是明确需求。
- 不引入 ORM、任务队列、前端构建系统等架构级依赖来解决局部问题。
- 不移动高风险模块位置，除非已有完整迁移计划和回归测试。
- 不删除兼容旧状态或旧配置格式的逻辑，除非确认历史数据无需兼容。
- 重构文件系统清理、外部同步、任务编排、LLM schema 时必须先补足保护性测试。

## 10. AI 执行任务时必须遵守的原则

- 先理解再修改：编码前阅读 `context.md`、本文档和相关源码/测试。
- 小步改动：优先做最小可行变更，保持现有架构和命名风格。不要添加无意义的炫技代码。
- 保护外部系统：任何可能访问 POP3、LLM、钉钉或删除文件的操作，都必须确认是否会产生真实副作用。
- 不碰无关文件：不要格式化、重排或重写与任务无关的文件。
- 不暴露秘密：不得展示、复制、记录 `.env` 中的真实凭据。
- 先测边界：涉及状态机、同步、任务入参、停止任务、提示词、文件清理的变更，必须覆盖失败和边界路径。
- 保持文档同步：行为、命令、配置、目录或长期规则变化时，更新 README 或 `docs/ai/project/` 中对应文档。
- 遇到不确定点要标记“待确认”，不要用猜测替代项目事实。
- 如果工具或环境限制导致无法验证，应明确说明限制、已做检查和剩余风险。
- 不把临时实现伪装成长期方案；确有临时方案时必须在文档或代码注释中说明原因和后续处理点。
