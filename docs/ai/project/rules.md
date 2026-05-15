# weekly-report-platform AI Coding 项目规则

生成日期：2026-05-14

执行代码修改前，应先阅读 `docs/ai/project/context.md` 和本文档。

## 1. 代码风格

- Python 3.10+ 风格，保留现有模块写法
- 新增函数和类补充类型标注，优先沿用 `dataclass`、`Path`
- 文件读写指定 `encoding="utf-8"`，JSON 写入 `ensure_ascii=False` + 可读缩进
- 异步只在确有并发收益时使用
- 日志统一 `weekly_report_platform.infrastructure.logging.logger`，不混用 `print()`
- 注释只解释业务规则和边界条件，不写"代码在做什么"
- 前端保持原生 HTML/CSS/JS，不引入构建链
- 错误信息可诊断、面向页面文案简短稳定

## 2. 命名规范

- 函数/变量 `snake_case`，类名 `PascalCase`，常量 `UPPER_SNAKE_CASE`
- 测试函数 `test_wrp_...` 前缀
- API 路径 `/api/tasks...`
- 状态值小写英文：`pending`、`running`、`synced`、`skipped`、`failed`
- 钉钉字段业务 key 保持语义稳定（如 `message_id`、`project_progress`）

## 3. 目录放置

- API 路由 → `api/`
- 邮件分析 → `domain/mail_analysis/`
- run 工作目录 → `domain/run_workspace/`
- 配置/日志 → `infrastructure/`
- 钉钉同步 → `dingtalk.py`（不拆散）
- 任务编排 → `runtime.py`
- `automation.py` 为遗留模块，不恢复或依赖
- 前端静态 → `web/static/`
- 默认配置 → `resources/`
- 测试 → `tests/`，命名对应被测模块
- AI 文档 → `docs/ai/project/`
- 运行产物 → `runs/`、`.state/` 或测试临时目录

## 4. 依赖

- 新依赖需明确必要性，优先标准库和已有依赖
- 运行依赖 → `requirements.txt`，开发依赖 → `requirements-dev.txt`
- 不引入重量级框架替代轻量实现
- 外部服务调用封装在边界模块（LLM → `llm_client.py`，钉钉 → `dingtalk.py`）
- 不硬编码敏感凭据，配置来自 `.env` 或已有加载机制
- POP3、LLM、mcporter 为外部副作用边界，测试中不得直接访问

## 5. 测试

默认命令：`python -m pytest -q --import-mode=importlib`

修改业务逻辑必须补充或更新对应测试。必须覆盖的场景：
- API：成功、冲突、停止、已移除路由
- 邮件解析：编码、HTML、附件、不支持类型
- LLM：monkeypatch/stub，空响应、非 JSON
- 钉钉同步：去重、多 target、字段缺失、mcporter 失败
- 任务编排：单任务锁、停止、参数解析
- 文件系统：使用临时目录

## 6. 禁止修改

- 不修改 `.env` 或暴露真实凭据
- 不随意修改 `config.local.json` 中真实 `baseId`、`tableId` 和字段映射
- 不随意改变 LLM 输出 JSON key（与 `sourcePath` 强耦合）
- 不随意改变 `message_id` 的提取、清洗和去重语义
- 不绕过"同一时刻只允许一个活动任务"限制
- 不重新引入持续执行/自动拉取页面或 `/api/automation...` 路由
- 不让测试访问真实 POP3、LLM、钉钉 MCP
- 不为局部需求大规模重写前端、编排、同步层或配置加载
- 不提交 `.venv/`、`__pycache__/`、`.tmp_testdata/`、`runs/`、`.state/` 等生成内容

## 7. 公共模块修改规范

- **runtime.py**：必须评估状态机、线程锁、停止语义和前端轮询
- **dingtalk.py**：必须评估字段映射、sourcePath、去重和多 target 聚合
- **prompts.py**：必须同步检查钉钉配置和文本格式化逻辑，任何 schema 变化需测试覆盖
- **email_parser.py**：必须覆盖编码、MIME、HTML、附件和临时文件
- **config.py**：必须保持环境变量优先级兼容性
- **phase5-app.js**：必须同步验证 API 响应、按钮禁用和轮询逻辑；FormData 取值必须在禁用控件前

## 8. 新增文件

- Python 源码放最贴近职责的包目录
- 测试放 `tests/`，命名 `test_*.py`
- 配置样例避免真实敏感信息
- 静态资源放 `web/static/`，检查 `index.html` 引用
- 新增文件 UTF-8，中文业务文案保留中文

## 9. 重构限制

- 不为"看起来更整洁"做无业务收益的大规模重构
- 不在同一变更中混合重构和行为修改
- 不改变公共入口函数签名（除非已同步所有调用方）
- 不引入 ORM、任务队列、前端构建系统等架构级依赖
- 不删除兼容旧状态的逻辑（除非确认无需兼容）

## 10. AI 执行原则

- 先理解再修改，编码前读 context.md 和相关源码
- 最小可行变更，保持现有架构和命名风格
- 保护外部系统：POP3、LLM、钉钉操作必须确认真实副作用
- 不碰无关文件
- 不暴露 `.env` 凭据
- 涉及状态机、同步、提示词的变更必须覆盖边界路径
- 遇不确定点标记"待确认"，不猜测