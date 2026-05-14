# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

`weekly-report-platform` 是一个周报邮件按需抽取、分析与同步平台。从 POP3 邮箱拉取周报邮件，解析正文和附件，调用 LLM 抽取结构化结果，同步到钉钉多维表，通过 FastAPI 页面统一管理任务。

## 重要约束

1. **不要擅自使用 git 修改、创建分支** — git 操作由人主导，除非明确要求。
2. **保持代码简洁、工程化** — 不要写炫技型代码，优先可读性和可维护性。
3. **分阶段推进功能模块** — 执行 AI Coding 时，应将功能模块拆分，分阶段逐步实现，不要一次性大范围修改。
4. **使用中文沟通** — 所有回复、注释、 commit 信息等使用中文。
5. **保持低耦合** — 模块之间通过明确接口通信，避免隐式依赖和循环引用。

## 常用命令

```powershell
# 环境初始化
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

# 启动后端服务（默认 http://127.0.0.1:8000）
python -m weekly_report_platform.api.app

# 运行测试
python -m pytest -q --import-mode=importlib
```

## API 端点

- `POST /api/tasks` — 启动单次抽取任务（请求体：`start_date`, `subject_keyword`, `force_refresh`, `max_emails`）
- `POST /api/tasks/stop` — 停止当前任务
- `GET /api/tasks/{run_id}` — 查询任务状态

## 项目结构

```
weekly_report_platform/
├── api/              # FastAPI 应用与路由
├── domain/
│   ├── mail_analysis/    # 邮件抓取、LLM 调用、提示词、分析
│   └── run_workspace/    # 运行工作目录管理
├── infrastructure/   # 配置加载、日志
├── resources/        # 钉钉字段配置
├── web/static/       # 前端静态资源
├── runtime.py        # 任务编排（TaskManager）
├── dingtalk.py       # 钉钉同步
├── processed_mail.py # 已处理邮件去重
└── automation.py     # 遗留模块，当前未接入
```

## 关键约定

- 同一时间只允许一个活动任务；任务运行中无法启动新任务
- 外部系统（POP3、LLM、钉钉）在测试中必须使用 monkeypatch 隔离
- LLM 提示词 JSON schema 与钉钉 `sourcePath` 强耦合，修改时需同步评估
- 配置优先级：`DINGTALK_CONFIG_JSON` > `DINGTALK_CONFIG_PATH` > 默认 `resources/config.local.json`
- 所有新增/修改必须有对应测试覆盖，不要只依赖手工验证

## 文档

- 项目上下文：`docs/ai/project/context.md`
- 编码规则：`docs/ai/project/rules.md`
