# weekly-report-platform

这个项目现在只保留一套使用方式：

1. 启动后端服务。
2. 打开前端页面。
3. 每次在页面选择本次抽取参数并启动任务。

## 项目作用

- 从 POP3 邮箱拉取周报邮件。
- 解析正文和附件。
- 调用 LLM 抽取结构化结果。
- 把结果同步到钉钉多维表。
- 通过 FastAPI 页面统一管理单次抽取、任务进度和停止任务。

## 当前结构

```text
.
├── weekly_report_platform/
│   ├── api/
│   ├── domain/
│   │   ├── mail_analysis/
│   │   └── run_workspace/
│   ├── infrastructure/
│   ├── resources/
│   ├── web/static/
│   ├── dingtalk.py
│   ├── processed_mail.py
│   └── runtime.py
├── tests/
├── requirements.txt
└── requirements-dev.txt
```

## 环境准备

- Python 3.10+
- Node.js
- 可用的 `mcporter` 或 `npx.cmd mcporter`
- 项目根目录下的 `.env`

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

`.env` 至少需要这些配置：

```env
EMAIL_POP3_HOST=pop3.example.com
EMAIL_POP3_PORT=995
EMAIL_USERNAME=your_mailbox
EMAIL_PASSWORD=your_password
EMAIL_SUBJECT_KEYWORD=周报

LLM_URL=https://your-llm-endpoint
LLM_MODEL=your-model
LLM_AUTHORIZATION=Bearer xxx

DINGTALK_MCP_URL=https://your-mcp-gateway
```

钉钉配置文件默认是 `weekly_report_platform/resources/config.local.json`，结构已经简化成单一配置：

```json
{
  "fields": {
    "message_id": {
      "fieldName": "邮件标识",
      "type": "text",
      "required": false,
      "sourcePath": "message_id"
    }
  },
  "targets": [
    {
      "baseId": "your-base-id",
      "tableId": "your-table-id"
    }
  ]
}
```

如果要改路径，可以在 `.env` 里设置：

```env
DINGTALK_CONFIG_PATH=weekly_report_platform/resources/config.local.json
```

## 启动方式

```powershell
python -m weekly_report_platform.api.app
```

默认地址：
- 页面：`http://127.0.0.1:8000/`
- 手动触发任务：`POST /api/tasks`
  请求体可包含本次抽取参数：`start_date`、`subject_keyword`、`force_refresh`、`max_emails`。
- 停止当前任务：`POST /api/tasks/stop`
- 查询任务状态：`GET /api/tasks/{run_id}`

本次抽取参数规则：
- `start_date` 为空时，扫描邮箱历史内容；传入日期时，只抽取该日期之后的邮件。
- `subject_keyword` 为空时，默认使用 `周报`。
- `max_emails` 为空时，抽取满足其他条件的全部邮件；传入正整数时，按数量限制抽取。
- 只要后台任务正在执行，就不能启动新任务；可以通过页面按钮或 `POST /api/tasks/stop` 请求停止当前任务。

## 运行产物

```text
runs/
  <run_id>/
    run_context.json
    mail/
      risk_json/
    sync/
      sync_manifest.json
    logs/
```

本地状态文件：

```text
.state/
  processed_message_ids.json
```

## 测试

```powershell
python -m pytest -q --import-mode=importlib
```
